'''
This file shoulnd't be modified or run for any purpose. Place it in your 
working directory, and import it's functions to use it. For more please
information about using SHDR please refer to the user manual.
'''

import multiprocessing as mp
from dataclasses import dataclass
import numpy as np
import pandas as pd
from tqdm import tqdm

@dataclass
class FitOptions:
    only_mld: bool = False
    CR: float = 0.7
    FF: float = 0.6
    num_generations: int = 1200
    num_individuals: int = 60
    max_b2_c2: float = 0.5
    exp_limit: float = 0.5
    min_depth: float = 100
    max_depth: float = 1000
    min_obs: int = 6
    tol: float = 0.00025
    seed: int = None


def process_input_field(arr):
    if isinstance(arr, np.ma.core.MaskedArray):
        processed_array = arr.astype(float).filled(np.nan)

    else:
        processed_array = np.asarray(arr, dtype=np.float64)

    return np.squeeze(processed_array)


def check_input(time, variable, depth, lat, lon):

    # make sure to always work with np.ndarray
    t = process_input_field(time)
    y = process_input_field(variable)
    z = process_input_field(depth)

    # check if latitude and longitude are provided and check their length
    if lat is None or lon is None:

        if lat is not lon:
            raise ValueError('Either neither or both lat and lon must be provided.')

    else:
        lat = process_input_field(lat)
        lon = process_input_field(lon)

        if time.shape != lat.shape or time.shape != lon.shape:
            raise ValueError('lat and lon arrays must have the same length as time')
         
    # length and size checks to ensure input arrays are compatible
    if time.ndim != 1:
        raise ValueError('Time must be 1-D array.')

    if variable.ndim != 2 or depth.ndim != 2:
        raise ValueError('Depth and variable must be 2-D arrays.')

    if time.shape[0] != variable.shape[0] or time.shape[0] != depth.shape[0]:
        raise ValueError('First dimension of variable and depth arrays must have the same length as time')
    
    return time, variable, depth, lat, lon


def fit_function(individuals, z, opts: FitOptions):
    '''Estimate the function a group of individuals at a height z'''
		
    limit = opts.exp_limit
    D1, b2, c2, b3, a2, a1 = np.split(individuals, 6, axis=1)

    pos = np.where(z >= D1, 1.0, 0.0)
    exponent = - (z -D1) * (b2 + (z - D1) * c2)
    
    # chech if exponent is inside limits
    exponent = np.where(exponent > limit, limit, exponent)
    exponent = np.where(exponent < - limit, - limit, exponent)

    return a1 + pos * (b3 * (z - D1) + a2 * (np.exp(exponent) - 1.0))


def get_fit_limits(y, z, opts: FitOptions):
    '''Returns the limits for the parametres of the fit function given a certain
       profile with meassures y at heights z.'''
       
    z = np.abs(z) # in case heights are defined negative

    min_z, max_z = z.min(), z.max()
    min_y, max_y = y.min(), y.max()
    
    lims = np.array([[1.0, max_z],    # D1
            [0.0, opts.max_b2_c2],    # b2
            [0.0, opts.max_b2_c2],    # c2
            [0.0 if max_z < opts.min_depth else - abs((max_y - min_y) / (max_z - min_z)), 0.0], # b3
            [0.0, max_y - min_y],     # a2
            [min_y, max_y]])          # a1
            

    lims_min = lims[:, 0]
    lims_max = lims[:, 1]
    
    return lims_min, lims_max


def random_init_population(y, z, opts: FitOptions):
    ''' Returns a random population of solutions of size num_individuals 
    initialized randomly with values inside limits for a profile with meassures
    y at heights z '''
    
    n = opts.num_individuals 
    lims_min, lims_max = get_fit_limits(y, z, opts)
    n_var = np.size(lims_max)
    
    norm = lims_max - lims_min
    individuals = lims_min + norm * np.random.random((n, n_var))

    return individuals


def population_fitness(individuals, y, z, opts):
    '''Estimate the fitting for a group of individuals via mean squared error'''
    
    fitness = np.sqrt(np.sum((y - fit_function(individuals, z, opts))**2, axis=1) / len(y))
    return fitness


def diferential_evolution(individuals, y, z, lims, opts):

    n = opts.num_individuals
    lims_min, lims_max = lims
    n_var = np.size(lims_max)
     
    present_fitns = population_fitness(individuals, y, z, opts)

    best_fit_loc = present_fitns.argmin()
    best_fit = individuals[best_fit_loc]

    for generation in range(opts.num_generations):

        # weight of best indivual is most important in later generations
        best_weight = 0.2 + 0.8 * (generation / opts.num_generations)**2
        
        # generate random permutations 
        perm_1 = np.random.permutation(n)
        perm_2 = np.random.permutation(n)
        new_gen = (1 - best_weight) * individuals + best_weight * best_fit + (opts.FF
                  * (individuals[perm_1] - individuals[perm_2]))
        
        new_gen = np.where(np.random.rand(n, n_var) < opts.CR,
                  new_gen, individuals)
                             

        # seting limits
        new_gen = np.where(new_gen < lims_min.reshape((1,6)), lims_min.reshape((1,6)), new_gen)
        new_gen = np.where(new_gen > lims_max.reshape((1,6)), lims_max.reshape((1,6)), new_gen)

        new_fitns = population_fitness(new_gen, y, z, opts)

        
        # update individuals to new generation
        individuals = np.where(present_fitns[:, None] < new_fitns[:, None], individuals, new_gen)
        present_fitns = np.where(present_fitns < new_fitns, present_fitns, new_fitns)

        best_fit_loc = present_fitns.argmin()
        best_fit = individuals[best_fit_loc]
        
        if present_fitns.mean() * opts.tol / present_fitns.std() > 1:
            break

     
    return best_fit, present_fitns[best_fit_loc]


def fit_profile(y, z, opts): 
    '''Parse and fit data from a single profile'''

    
    # remove nans in both arrays
    y = y[np.isfinite(z)]
    z = z[np.isfinite(z)]

    z = z[np.isfinite(y)]
    y = y[np.isfinite(y)]
    
    # only use depths until max_depth
    if (z > opts.max_depth).any():
        max_z_idx = np.argmax(z > opts.max_depth)
        z = z[:max_z_idx]
        y = y[:max_z_idx]
    
    if len(z) < opts.min_obs:
        return np.repeat(np.nan, 8)
    
    lims_min, lims_max = get_fit_limits(y, z, opts)
    

    lims = (lims_min, lims_max)

    first_gen = random_init_population(y, z, opts)
    result_1, fitness_1 = diferential_evolution(first_gen, y, z, lims, opts)  
    
    
    #### DELTA CODING ####
    
    # set new limits for fit in function of previous fit result
    # and have them meet the physical limits
    v_min, v_max = 0.85 * result_1, 1.15 * result_1
    for i in range(6):
        lim_min_d = min(v_min[i], v_max[i])
        lim_max_d = max(v_min[i], v_max[i])
        lims_min[i] = max(lims_min[i], lim_min_d)
        lims_max[i] = max(lims_max[i], lim_max_d)
    lims_delta = (lims_min, lims_max)

    first_gen = random_init_population(y, z, opts)   # new first generation

    result_delta, fitness_delta = diferential_evolution(first_gen, y, z, lims, opts)


    if fitness_1 < fitness_delta:
        result = result_1
        fitness = fitness_1 
    else:
        result = result_delta
        fitness = fitness_delta 

    D1, b2, c2, b3, a2, a1 = result
    em = fitness
    a3 = a1 - a2 
    return np.array([D1, b2, c2, b3, a2, a1, a3, em])


def format_result(result, time, lat, lon, opts):
      
    if opts.only_mld == True:
        columns = ['D1', 'em']
        result_df = pd.DataFrame([[i[0], i[-1]] for i in result], columns=columns)
    
    else:
        columns = ['D1', 'b2', 'c2', 'b3', 'a2', 'a1', 'a3', 'em']
        result_df = pd.DataFrame(result, columns=columns)
    
    result_df.set_index(time, inplace=True)

    if lat is not None:
        result_df.insert(1, 'lat', lat)
        result_df.insert(2, 'lon', lon)

    return result_df


def run_multiprocessing_fit_pool(variable, depth, opts):
    n = variable.shape[0]
    pool_arguments = [[variable[i, :], depth[i, :], opts] for i in range(n)]
    with mp.Pool(processes=mp.cpu_count()) as pool:
        results_fit = pool.starmap(fit_profile, tqdm(pool_arguments,
                                                     total=len(pool_arguments)), chunksize=1)
    return results_fit


def fit_time_series(time, variable, depth, lat=None, lon=None, **opts):
     
    time, variable, depth, lat, lon = check_input(time, variable, depth, lat, lon) 
    opts = FitOptions(**opts) 
    
    results_fit = run_multiprocessing_fit_pool(variable, depth, opts)
    
    result_df = format_result(results_fit, time, lat, lon, opts)
    return result_df