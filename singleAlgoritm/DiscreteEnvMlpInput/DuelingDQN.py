'''
implemented by PyTorch.
'''
import numpy as np
import torch.nn as nn
import torch
from torch.optim import Adam
import os


class ReplayBuffer:
    def __init__(self, state_dim, max_size=10000, device=torch.device('cpu')):
        self.device = device
        self.state_buffer = torch.empty((max_size, state_dim), dtype=torch.float32, device=device)
        self.other_buffer = torch.empty((max_size, 3), dtype=torch.float32, device=device)
        self.index = 0
        self.max_size = max_size
        self.total_len = 0

    def append(self, state, other):
        self.index = self.index % self.max_size
        self.total_len = max(self.index, self.total_len)
        self.state_buffer[self.index] = torch.as_tensor(state, device=self.device)
        self.other_buffer[self.index] = torch.as_tensor(other, device=self.device)
        self.index += 1

    def sample_batch(self, batch_size):
        indices = np.random.randint(0, self.total_len - 1, batch_size)
        return (
            self.state_buffer[indices],  # S_t
            self.other_buffer[indices, 2:].long(),  # a_t
            self.other_buffer[indices, 0],  # r_t
            self.other_buffer[indices, 1],  # done
            self.state_buffer[indices + 1]
        )


class DuelingQNet(nn.Module):
    def __init__(self, obs_dim: int, action_dim: int, mid_dim: int = 256) -> None:
        '''
        :param obs_dim:  the dim of observation. type: int. for gym env: obs_dim = env.observation_space.shape[0]
        :param action_dim: action space, i.e: The number of actions that can be taken at each step. type:int. for gym env: action_dim = env.action_space.n
        :param mid_dim: hidden size of MLP.
        '''
        super(DuelingQNet, self).__init__()
        self.obs_dim = obs_dim
        self.action_dim = action_dim
        self.encoder = nn.Sequential(
            nn.Linear(obs_dim, mid_dim), nn.ReLU(),
            nn.Linear(mid_dim, mid_dim), nn.ReLU(),
        )
        self.net_val = nn.Sequential(
            nn.Linear(mid_dim, mid_dim), nn.ReLU(),
            nn.Linear(mid_dim, 1))
        self.net_adv = nn.Sequential(
            nn.Linear(mid_dim, mid_dim), nn.ReLU(),
            nn.Linear(mid_dim, action_dim))

    def forward(self, state: torch.FloatTensor) -> torch.FloatTensor:
        # return Q(s, a). the estimated state-action value.
        state = self.encoder(state)
        q_val = self.net_val(state)
        q_adv = self.net_adv(state)
        return q_val + q_adv - q_adv.mean(dim=1, keepdim=True)


class DuelingDQNAgent:
    def __init__(self, obs_dim: int, action_dim: int):
        self.obs_dim = obs_dim
        self.action_dim = action_dim
        self.learning_tate = 1e-4
        self.tau = 2 ** -8  # soft update.
        self.gamma = 0.99  # discount factor.
        self.batch_size = 512
        self.memory_size = 1000000
        self.explore_rate = 0.2  # epsilon greedy rate.
        '''
        for exploring in the env, each time will collect self.target_step * self.batch_size number of samples into buffer,
        for updating neural network, each time will update self.target_step * self.repeat_time times. 
        '''
        self.target_step = 4096
        self.repeat_time = 128
        self.reward_scale = 1.
        self.device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
        self.buffer = ReplayBuffer(obs_dim, self.memory_size, self.device)
        self.DuelingQNet = DuelingQNet(obs_dim, action_dim).to(self.device)
        self.DuelingQNet_target = DuelingQNet(obs_dim, action_dim).to(self.device)  # Q target.
        self.optimizer = Adam(self.DuelingQNet.parameters(), self.learning_tate)
        self.loss_func = nn.MSELoss(reduction='mean')

    def select_action(self, state: np.ndarray) -> int:
        # using epsilon greedy algorithm to select the action.
        if np.random.random() < self.explore_rate:  # epsilon greedy.
            action = np.random.randint(self.action_dim)
        else:
            state = torch.as_tensor((state,), dtype=torch.float32, device=self.device).detach_()
            dist = self.DuelingQNet(state)[0]
            action = dist.argmax(dim=0).cpu().numpy()
        return action

    def explore_env(self, env, all_greedy=False) -> int:
        # to collect samples into replay buffer.
        state = env.reset()
        for _ in range(self.target_step):
            action = np.random.randint(self.action_dim) if all_greedy else self.select_action(state)
            state_, reward, done, _ = env.step(action)
            other = (reward * self.reward_scale, 0.0 if done else self.gamma, action)
            self.buffer.append(state, other)
            state = env.reset() if done else state_
        return self.target_step

    @staticmethod
    def soft_update(eval_net, target_net, tau) -> None:
        # soft update for network. the equation: W_1 * tau + W_2 * (1 - tau)
        for target_param, local_param in zip(target_net.parameters(), eval_net.parameters()):
            target_param.data.copy_(tau * local_param.data + (1.0 - tau) * target_param.data)

    def update(self) -> None:
        # update the neural network.
        for _ in range(int(self.target_step * self.repeat_time / self.batch_size)):
            state, action, reward, mask, state_ = self.buffer.sample_batch(self.batch_size)
            # Q(s_t, a_t) = r_t + \gamma * max Q(s_{t+1}, a)
            next_q = self.DuelingQNet_target(state_).detach().max(1)[0]
            q_target = reward + mask * next_q
            q_eval = self.DuelingQNet(state).gather(1, action)
            loss = self.loss_func(q_eval, q_target.view(self.batch_size, 1))
            self.optimizer.zero_grad()
            loss.backward()
            self.optimizer.step()
            self.soft_update(self.DuelingQNet, self.DuelingQNet_target, self.tau)

    def evaluate(self, env, render=False):
        epochs = 20
        res = np.zeros((epochs,))
        obs = env.reset()
        index = 0
        while index < epochs:
            if render: env.render()
            obs = torch.as_tensor((obs,), dtype=torch.float32, device=self.device).detach_()
            dist = self.DuelingQNet(obs)[0]
            action = dist.argmax(dim=0).cpu().numpy()
            s_, reward, done, _ = env.step(action)
            res[index] += reward
            if done:
                index += 1
                obs = env.reset()
            else:
                obs = s_
        return res.mean(), res.std()

    def load_and_save_weight(self, path, mode='load'):
        if mode == 'load':
            if os.path.exists(path):
                self.DuelingQNet.load_state_dict(torch.load(path))
                self.DuelingQNet_target.load_state_dict(torch.load(path))
        else:
            torch.save(self.DuelingQNet.state_dict(), path)
