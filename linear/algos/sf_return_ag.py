# =============================================================================
# Linear successor lambda return agent
#
# Author: Anthony G. Chen
# =============================================================================

import copy
from typing import List, Tuple

from gym import spaces
import numpy as np

from algos.base import BaseLinearAgent


class SFReturnAgent(BaseLinearAgent):
    def __init__(self, feature_dim,
                 num_actions,
                 gamma=0.9,
                 lamb=0.8,
                 lr=0.1,
                 seed=0):
        """
        TODO define arguments
        :param feature_dim:
        :param num_actions:
        :param gamma:
        :param lamb:
        :param lr:
        :param seed:
        """
        super().__init__(feature_dim, num_actions, gamma=gamma, lr=lr, seed=seed)

        self.lamb = lamb
        self.reward_lr = lr  # different learning rates?
        self.value_lr = lr
        self.sf_lr = lr

        # Weights
        self.Wr = np.zeros(self.feature_dim)
        self.Ws = np.zeros((self.num_actions,
                            self.feature_dim,
                            self.feature_dim))  # |A| * d * D
        self.Wv = np.zeros(self.feature_dim)

        # (Optional) Give agent the optimal reward parameters
        self.use_true_R_fn = False

    def begin_episode(self, phi):
        super().begin_episode(phi)

        self.log_dict = {
            'reward_errors': [],
            'sf_error_norms': [],
            'value_errors': [],
        }

    def step(self, phi_t: np.array, reward: float, done: bool) -> int:
        # Get new action based on state
        new_act = self._select_action(phi_t)

        # Save trajectory
        if not done:
            self.traj['phi'].append(phi_t)
            self.traj['a'].append(new_act)
        self.traj['r'].append(reward)

        # ==
        # Learning

        # Reward learning
        if (not self.use_true_R_fn) and (len(self.traj['r']) > 0):
            self._optimize_reward_fn()

        # SF learning
        if len(self.traj['phi']) > 1:
            self._optimize_successor_features(done)

        # Value learning
        if len(self.traj['phi']) > 0:
            self._optimize_value_fn()

        return new_act

    def _optimize_reward_fn(self) -> None:
        # NOTE: We learn apping phi_t -> r_{t+1} to properly account for all
        #       rewards, even though theory says we map phi_t -> r_t.
        #       (We can pretend r_{t+1} is r_{t})
        # Get most recent feature & reward
        t_idx = len(self.traj['r']) - 1
        cur_phi = self.traj['phi'][t_idx]
        cur_rew = self.traj['r'][t_idx]

        # Update reward function
        rew_err = cur_rew - np.dot(cur_phi, self.Wr)
        d_Wr = rew_err * cur_phi
        self.Wr = self.Wr + (self.reward_lr * d_Wr)

        # (Log) Reward error
        self.log_dict['reward_errors'].append(d_Wr)

    def _optimize_successor_features(self, done) -> None:
        # Get current experience tuple (S, A)
        t_idx = len(self.traj['phi']) - 2
        cur_phi = self.traj['phi'][t_idx]
        cur_act = self.traj['a'][t_idx]
        nex_phi = self.traj['phi'][t_idx + 1]

        # Get next experience tuple if present (S', A')
        if not done:
            nex_act = self.traj['a'][t_idx+1]
            nex_sf = np.transpose(self.Ws[nex_act]) @ nex_phi  # NOTE transpose?
        else:
            nex_sf = 0.0

        # Compute SF TD errors
        cur_sf = np.transpose(self.Ws[cur_act]) @ cur_phi  # NOTE transpose?
        sf_td_err = nex_phi + (self.lamb * self.gamma * nex_sf) - cur_sf  # (d, )

        d_Ws = np.transpose(np.outer(sf_td_err, cur_phi))  # NOTE transpose?

        # Update
        self.Ws[cur_act] += self.sf_lr * d_Ws
        # Maybe future TODO can use soft actions

        # (Log) Norm of the SF error vector
        self.log_dict['sf_error_norms'].append(
            np.linalg.norm(sf_td_err)
        )

    def _optimize_value_fn(self) -> None:
        # Get current feature
        t_idx = len(self.traj['phi']) - 1
        cur_phi = self.traj['phi'][t_idx]
        cur_act = self.traj['a'][t_idx]

        # Compute successor lambda return
        sl_G = self.compute_successor_return(cur_phi, cur_act)

        sl_err = (sl_G - (cur_phi.T @ self.Wv))
        d_Wv = sl_err * cur_phi

        # Update
        self.Wv += self.value_lr * d_Wv

        # (Log) Value function error
        self.log_dict['value_errors'].append(sl_err)

    def compute_successor_return(self, phi, act) -> float:
        """
        Helper function, compute the value estimate using the lambda
        successor feature return
        :param phi: state featureat time t
        :param act: action
        :return: value, Q(phi, act)_t
        """
        cur_sf_T = phi.T @ self.Ws[act]  # (d, )  # NOTE transpose?
        sl_G = cur_sf_T @ (self.Wr + (self.gamma * (1.0 - self.lamb) * self.Wv))  # scalar
        return sl_G

    def compute_Q_value(self, phi, act) -> float:
        """
        Helper function, compute the value given a state feature and action
        using just the value function parameters
        NOTE: using Q for compatibility but it should be V function

        :return: value, Q(phi, act)
        """
        return np.dot(phi, self.Wv)

    def _select_action(self, phi) -> int:
        # TODO: change this for control (set policy here)
        # for now only does policy eval 
        return 0


# ==
# For testing purposes only
if __name__ == "__main__":
    pass
