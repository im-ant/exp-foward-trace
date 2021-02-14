# =============================================================================
# Training the linear agent on linear prediction environments
#
# Author: Anthony G. Chen
# =============================================================================

import argparse
import configparser
import dataclasses
from itertools import product
import json
import logging
import numbers
import os
import uuid

import gym
import hydra
from omegaconf import DictConfig, OmegaConf
import numpy as np

from algos.q_learning import QAgent
from algos.sf_q_learning import LambdaSFQAgent
from envs.lehnert_grid import LehnertGridWorldEnv
import utils.mdp_utils as mut


@dataclasses.dataclass
class LogTupStruct:
    num_episodes: int = None
    envCls_name: str = None
    env_kwargs: str = None
    agentCls_name: str = None
    seed: int = None
    gamma: float = None
    lr: float = None
    sf_lr: float = None
    optim_kwargs: str = None
    reward_lr: float = None
    lamb: float = None
    eta_trace: float = None
    policy_epsilon: float = None
    use_true_reward_params: bool = None
    use_true_sf_params: bool = None
    episode_idx: int = None  # episodic-specific logs
    total_steps: int = None
    cumulative_reward: float = None
    v_fn_rmse: float = None
    sf_G_rmse: float = None
    sf_matrix_rmse: float = None
    reward_vec_rmse: float = None
    value_loss_avg: float = None  # agent log dict specific logs
    reward_loss_avg: float = None
    sf_loss_avg: float = None
    et_loss_avg: float = None


def init_logger(logging_path: str) -> logging.Logger:
    """
    Initializes the path to write the log to
    :param logging_path: path to write the log to
    :return: logging.Logger object
    """
    logger = logging.getLogger('Experiment')

    logger.setLevel(logging.INFO)

    file_handler = logging.FileHandler(logging_path)
    formatter = logging.Formatter('%(asctime)s||%(message)s')
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger


def _initialize_environment(cfg: DictConfig) -> object:
    """
    Helper function to initialize the environment object
    :param cfg:
    :return: gym.Env object
    """
    envCls = globals()[cfg.env.cls_string]  # hacky
    env_kwargs = OmegaConf.to_container(cfg.env.kwargs)  # convert to dict
    env_kwargs['seed'] = cfg.training.seed  # add global seed
    environment = envCls(**env_kwargs)
    return environment


def _initialize_agent(cfg: DictConfig, environment) -> object:
    """
    Helper method to initialize an agent object
    :param cfg: hydra config dict
    :param environment: gym environment
    :return: agent object
    """

    # ==
    # Initialize with config parameters
    agentCls = globals()[cfg.agent.cls_string]  # hacky class init
    agent_kwargs = OmegaConf.to_container(cfg.agent.kwargs)  # convert to dict
    agent_kwargs['seed'] = cfg.training.seed

    # Initialize and return
    agent = agentCls(
        feature_dim=environment.observation_space.shape[0],  # assume linear
        num_actions=environment.action_space.n,
        **agent_kwargs
    )

    # ==
    # (Optional) Initialize true reward and SF functions

    # Initialize solved reward parameters
    """ TODO fix
    if hasattr(cfg.agent.kwargs, 'use_true_reward_params'):
        if cfg.agent.kwargs.use_true_reward_params:
            linear_reward_param = mut.solve_linear_reward_param(
                env=environment,
            )
            agent.Wr = linear_reward_param
    """

    return agent


def _extract_agent_log_dict(agent):
    """
    Helper method to extract the agent's logged items for the logger
    :param agent: agent object
    :return: dictionary for epis_log_dict
    """
    # The output log dictionary
    # NOTE: need ot make this fit with the keys of LogTupStruct
    log_dict = {
        'value_loss_avg': None,
        'reward_loss_avg': None,
        'sf_loss_avg': None,
        'et_loss_avg': None,
    }

    # ==
    # Extract agent log
    ag_dict = agent.log_dict
    if ag_dict is None:
        return log_dict

    # Compute
    # TODO: add more conditions to check if the list is empty?
    if 'value_errors' in ag_dict:
        avg_value_loss = np.average(
            np.square(ag_dict['value_errors'])
        )
        log_dict['value_loss_avg'] = avg_value_loss

    if 'reward_errors' in ag_dict:
        avg_rew_loss = np.average(
            np.square(ag_dict['reward_errors'])
        )
        log_dict['reward_loss_avg'] = avg_rew_loss

    if 'sf_error_norms' in ag_dict:
        # NOTE: it is already the norm so positive
        avg_sf_loss = np.average(ag_dict['sf_error_norms'])
        log_dict['sf_loss_avg'] = avg_sf_loss

    if 'et_error_norms' in ag_dict:
        # NOTE: it is already the norm so positive
        avg_et_loss = np.average(ag_dict['et_error_norms'])
        log_dict['et_loss_avg'] = avg_et_loss

    return log_dict


def write_post_episode_log(cfg: DictConfig,
                           episode_dict: dict,
                           environment, agent,
                           logger) -> None:
    log_dict = {
        'num_episodes': cfg.training.num_episodes,
        'envCls_name': cfg.env.cls_string,
        'env_kwargs': str(cfg.env.kwargs),
        'agentCls_name': cfg.agent.cls_string,
        'seed': cfg.training.seed,  # global seed
        'lr': cfg.agent.lr,
    }

    # Assume all agent kwargs are available in log
    for k in cfg.agent.kwargs:
        if isinstance(cfg.agent.kwargs[k], numbers.Number):
            log_dict[k] = cfg.agent.kwargs[k]
        else:
            log_dict[k] = str(cfg.agent.kwargs[k])

    # ==
    # episode-specific logs

    log_dict['episode_idx'] = episode_dict['episode_idx']
    log_dict['total_steps'] = episode_dict['total_steps']
    log_dict['cumulative_reward'] = episode_dict['cumulative_reward']

    # ==
    # Get losses from the agent logs
    agent_log_dict = _extract_agent_log_dict(agent)
    for k in agent_log_dict:
        log_dict[k] = agent_log_dict[k]

    # ==
    # Write the output log

    # Construct log output
    logStructDict = dataclasses.asdict(LogTupStruct(**log_dict))
    log_str = '||'.join([str(logStructDict[k]) for k in logStructDict])
    # Write or print
    if logger is not None:
        logger.info(log_str)
    else:
        if (episode_idx + 1) % args.log_print_freq == 0:  # I think this is not used anymore
            print(log_str)


def post_episode_updates(cfg: DictConfig, episode_idx: int,
                         agent, environment) -> None:
    """
    Post-episode manipulations
    :param cfg:
    :param episode_idx:
    :param agent:
    :param environment:
    :return:
    """
    # ==
    # Reset parameters
    if cfg.training.param_reset.freq is not None:
        if (episode_idx + 1) % cfg.training.param_reset.freq == 0:
            # Get attributes
            attr_str_list = cfg.training.param_reset.attr_strs.split(';')

            # Optionally reset reward parameters
            if 'Wr' in attr_str_list:
                agent.Wr = agent.rng.uniform(
                    0.0, 1e-5, size=agent.feature_dim
                )

            # Optionally reset SF parameters
            if 'Ws' in attr_str_list:
                agent.Ws = np.zeros(
                    (agent.num_actions, agent.feature_dim, agent.feature_dim)
                )
                ws_idxs = np.arange(agent.feature_dim)
                agent.Ws[:, ws_idxs, ws_idxs] = 1.0


def run_single_linear_experiment(cfg: DictConfig,
                                 logger=None):
    # ==================================================
    # Initialize environment
    environment = _initialize_environment(cfg)

    # ==================================================
    # Initialize agent
    agent = _initialize_agent(cfg, environment)

    # ==================================================
    # Run experiment
    for episode_idx in range(cfg.training.num_episodes):
        # Reset env
        obs = environment.reset()
        action = agent.begin_episode(obs)

        # Logging
        cumulative_reward = 0.0
        steps = 0

        while True:
            # Interact with environment
            obs, reward, done, info = environment.step(action)
            action = agent.step(obs, reward, done)

            # print(f'state: {environment.state}, r: {reward}, done: {done}, a: {action}')  # TODO DELETE

            # Tracker variables
            cumulative_reward += reward
            steps += 1

            if done:
                # ==
                # Log
                post_epis_dict = {
                    'episode_idx': episode_idx,
                    'total_steps': steps,
                    'cumulative_reward': cumulative_reward,
                }
                write_post_episode_log(cfg=cfg,
                                       episode_dict=post_epis_dict,
                                       environment=environment,
                                       agent=agent,
                                       logger=logger)

                # ==
                # (Potential resets)
                post_episode_updates(cfg=cfg,
                                     episode_idx=episode_idx,
                                     agent=agent,
                                     environment=environment)

                # ==
                # Terminate
                break

        # (Optional) Write matrices
        if cfg.training.save_checkpoint is not None:
            if ((cfg.training.save_checkpoint > 0) and
                    (episode_idx % cfg.training.save_checkpoint == 0)):
                save_checkpoint(cfg, agent, episode_idx)


def save_checkpoint(cfg: DictConfig, agent, episode_idx):
    """
    Helper method to manually write to file
    :return:
    """
    ckpt_dict = {'episode_idx': episode_idx}

    # ==
    # Convert config to dict
    cfg_dict = OmegaConf.to_container(cfg)
    ckpt_dict['cfg'] = cfg_dict

    # ==
    # Save key parameters of agents
    ckpt_dict['agent'] = {}

    attri_list = ['Wq', 'Wz', 'Wr', 'Ws', 'Wv']
    for att_str in attri_list:
        if hasattr(agent, att_str):
            cur_att = getattr(agent, att_str)
            ckpt_dict['agent'][att_str] = cur_att.tolist()

    # ==
    # Write out
    out_dir_path = './checkpoint'  # TODO don't hard code?
    if not os.path.isdir(out_dir_path):
        os.mkdir(out_dir_path)
    rand_str = str(uuid.uuid4().hex)
    out_file_name = f'ckpt_epis-{episode_idx}' \
                    f'_{rand_str}.json'
    out_file_path = os.path.join(out_dir_path, out_file_name)

    with open(out_file_path, 'w') as outfile:
        json.dump(ckpt_dict, outfile)


def run_experiments(cfg: DictConfig, logger=None):
    """
    Wrapper method, takes Cartesian product of lists inside of the
    DictConfig before running individual experiments.
    :param cfg:
    :param logger:
    :return:
    """

    def dict_2_dict_list(d: dict) -> dict:
        """
        Convert all of a dict's leaf values into lists
        E.g. 1 -> [1]
        """
        # ==
        # Base case: if it is a list
        if isinstance(d, list):
            return d
        # Base case: if it is a primitive
        if not isinstance(d, dict):
            return [d]
        # ==
        # Recursion if it is a dict
        new_d = {}
        for k in d:
            new_d[k] = dict_2_dict_list(d[k])
        return new_d

    def gen_combinations(d: dict):
        """
        Recursive Cartesian product of a nested dictionary of lists of
        arbitrary length. Outputs a generator. From:
        https://stackoverflow.com/questions/50606454/cartesian-product-of-nested-dictionaries-of-lists
        :param d:
        :return:
        """
        keys, values = d.keys(), d.values()
        values_choices = (gen_combinations(v) if isinstance(v, dict) else v for v in values)
        for comb in product(*values_choices):
            yield dict(zip(keys, comb))

    # ==
    # Convert
    cfg_dict = OmegaConf.to_container(cfg)
    cfg_dict_list = dict_2_dict_list(cfg_dict)

    # ==
    # Iterate over product of possible param combinations
    for c in gen_combinations(cfg_dict_list):
        print(c)
        cur_cfg = OmegaConf.create(c)
        # run individual experiments
        run_single_linear_experiment(cur_cfg, logger)


@hydra.main(config_path="conf", config_name="config_control")
def main(cfg: DictConfig) -> None:
    print(OmegaConf.to_yaml(cfg))  # print the configs

    # =====================================================
    # Initialize logger
    dc_fields = [k for k in vars(LogTupStruct())]  # temp object to get name
    log_title_str = '||'.join(dc_fields)
    if cfg.logging.dir_path is not None:
        log_path = os.path.join(cfg.logging.dir_path, 'progress.csv')
        logger = init_logger(log_path)
        logger.info(log_title_str)
    else:
        print(log_title_str)
        logger = None

    # =====================================================
    # Initialize experiment
    # run_single_linear_experiment(cfg=cfg, logger=logger)  # for indv jobs
    run_experiments(cfg=cfg, logger=logger)  # sweep over config lists


if __name__ == "__main__":
    main()