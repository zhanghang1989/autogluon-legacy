import os
import json
import pickle
import copy
import logging
from collections import OrderedDict

# Suppress common skopt warning:
import warnings
warnings.filterwarnings("ignore", message="The objective has been evaluated at this point before.")

from skopt import Optimizer
from skopt.space import *

from .searcher import BaseSearcher

__all__ = ['SKoptSearcher']
logger = logging.getLogger(__name__)

class SKoptSearcher(BaseSearcher):
    """SKopt Searcher for ConfigSpace. Requires that 'scikit-optimize' package is installed.
    
    Args:
        configspace: ConfigSpace.ConfigurationSpace
            The configuration space to sample from. It contains the full
            specification of the Hyperparameters with their priors
        kwargs: Optional arguments passed to skopt.optimizer.Optimizer class,
                please see documentation at: http://scikit-optimize.github.io/optimizer/index.html#skopt.optimizer.Optimizer
            These kwargs be used to specify which surrogate model Bayesian optimization should rely on,
            which acquisition function to use, how to optimize the acquisition function, etc.
            The skopt library provides very comprehensive Bayesian optimization functionality,
            popular non-default kwargs options here might include: 
            - base_estimator = 'GP' or 'RF' or 'ET' or 'GBRT' (to specify different surrogate models like Gaussian Processes, Random Forests, etc)
            - acq_func = 'LCB' or 'EI' or 'PI' or 'gp_hedge' (to specify different acquisition functions like Lower Confidence Bound, Expected Improvement, etc)
            For example, we can tell our Searcher to perform Bayesian optimization with a Random Forest surrogate model
            and use the Expected Improvement acquisition function by invoking the following kwargs:
            SKoptSearcher(cs, base_estimator='RF', acq_func='EI').
    
    Example:
        >>> import ConfigSpace as CS
        >>> import ConfigSpace.hyperparameters as CSH
        >>> # create configuration space
        >>> cs = CS.ConfigurationSpace()
        >>> lr = CSH.UniformFloatHyperparameter('lr', lower=1e-4, upper=1e-1, log=True)
        >>> cs.add_hyperparameter(lr)
        >>> # create BayesOpt searcher which uses RF surrogate model and Expected Improvement acquisition: 
        >>> searcher = SKoptSearcher(cs, base_estimator='RF', acq_func='EI')
        >>> next_config = searcher.get_config()
        >>> next_reward = 10.0 # made-up value.
        >>> searcher.update(next_config, next_reward)
        
    Notes on SKopt behavior:
    
    - get_config() cannot ensure valid configurations for conditional spaces since skopt 
    does not contain this functionality like ConfigSpace does. 
    Currently SKoptSearcher.get_config() will catc these Exceptions and revert to random_config() in this case
    
    - get_config(max_tries) uses skopt batch BayesOpt functionality to query at most 
    max_tries number of configs to try out.
    If all of these have configus have already been scheduled to try (might happen in asynchronous setting), 
    then get_config simply reverts to random search via random_config().
    """
    
    def __init__(self, configspace, **kwargs):
        BaseSearcher.__init__(self, configspace)
        self.hp_ordering = configspace.get_hyperparameter_names() # fix order of hyperparams in configspace.
        skopt_hpspace = []
        for hp in self.hp_ordering:
            hp_obj = configspace.get_hyperparameter(hp)
            hp_type = str(type(hp_obj)).lower() # type of hyperparam
            if 'integer' in hp_type:
                hp_dimension = Integer(low=int(hp_obj.lower), high=int(hp_obj.upper),name=hp)
            elif 'float' in hp_type:
                if hp_obj.log: # log10-scale hyperparmeter
                    hp_dimension = Real(low=float(hp_obj.lower), high=float(hp_obj.upper), prior='log-uniform', name=hp)
                else:
                    hp_dimension = Real(low=float(hp_obj.lower), high=float(hp_obj.upper), name=hp)
            elif 'categorical' in hp_type:
                hp_dimension = Categorical(hp_obj.choices, name=hp)
            elif 'ordinal' in hp_type:
                hp_dimension = Categorical(hp_obj.sequence, name=hp)
            else:
                raise ValueError("unknown hyperparameter type: %s" % hp)
            skopt_hpspace.append(hp_dimension)
        self.bayes_optimizer = Optimizer(dimensions = skopt_hpspace, **kwargs)
    
    def get_config(self, max_tries=1e2):
        """Function to sample a new configuration
        This function is called to query a new configuration that has not yet been tried.
        Asks for one point at a time from skopt, up to max_tries. 
        If an invalid hyperparameter configuration is proposed by skopt, then reverts to random search
        (since skopt configurations cannot handle conditional spaces like ConfigSpace can).
        TODO: may loop indefinitely due to no termination condition (like RandomSearcher.get_config() ) 
        
        Args:
            max_tries: the maximum number of tries to ask for a unique config from skopt before
            reverting to random search.
            returns: (config, info_dict)
                must return a valid configuration and a (possibly empty) info dict
        """
        if len(self._results) == 0: # no hyperparams have been tried yet, first try default config
            return self.default_config()
        try:
            new_points = self.bayes_optimizer.ask(n_points=1) # initially ask for one new config
            new_config_cs = self.skopt2config(new_points[0]) # hyperparameter-config to evaluate
            new_config_cs.is_valid_configuration()
            new_config = new_config_cs.get_dictionary()
            if (json.dumps(new_config) not in self._results.keys()): # have not encountered this config
                self._results[json.dumps(new_config)] = 0
                return new_config
            new_points = self.bayes_optimizer.ask(n_points=max_tries) # ask skopt for many configs since first one was not new
            i = 1 # which new point to return as new_config, we already tried the first point above
            while i < max_tries:
                new_config_cs = self.skopt2config(new_points[i]) # hyperparameter-config to evaluate
                new_config_cs.is_valid_configuration()
                new_config = new_config_cs.get_dictionary()
                if (json.dumps(new_config) not in self._results.keys()): # have not encountered this config
                    self._results[json.dumps(new_config)] = 0
                    return new_config
                i += 1
        except ValueError:
            warnings.warn("skopt failed to produce new config, using random search instead")
        return self.random_config()
    
    def default_config(self):
        """ Function to return the default configuration that should be tried first.
        
        Args:
            returns: config
        """
        new_config_cs = self.configspace.get_default_configuration()
        new_config = new_config_cs.get_dictionary()
        self._results[json.dumps(new_config)] = 0
        return new_config
        
    def random_config(self):
        """Function to randomly sample a new configuration which must be valid.
           TODO: may loop indefinitely due to no termination condition (like RandomSearcher.get_config() ) 

        Args:
            returns: config
        """
        new_config = self.configspace.sample_configuration().get_dictionary()
        while json.dumps(new_config) in self._results.keys():
            new_config = self.configspace.sample_configuration().get_dictionary()
        self._results[json.dumps(new_config)] = 0
        return new_config

    def update(self, config, reward, model_params=None):
        """Update the searcher with the newest metric report
        """
        self._results[json.dumps(config)] = reward
        self.bayes_optimizer.tell(self.config2skopt(config), -reward) # provide negative reward since skopt performs minimization
        logger.info('Finished Task with config: {} and reward: {}'.format(json.dumps(config), reward))

    def config2skopt(self, config):
        """ Converts autogluon config (dict object) to skopt format (list object).
        Args:
            returns: object of same type as: skOpt.Optimizer.ask()
        """
        point = []
        for hp in self.hp_ordering:
            point.append(config[hp])
        return point
    
    def skopt2config(self, point):
        """ Converts skopt point (list object) to autogluon config format (dict object. 
        
        Args:
            returns: object of same type as: RandomSampling.configspace.sample_configuration().get_dictionary()
        """
        config = self.configspace.sample_configuration()
        for i in range(len(point)):
            hp = self.hp_ordering[i]
            config[hp] = point[i]
        return config