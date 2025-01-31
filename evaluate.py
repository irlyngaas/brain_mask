"""Evaluate a trained model using metrics specified in parameter file"""

import argparse
from glob import glob
import logging
import os
# set tensorflow logging level before importing things that contain tensorflow
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '2'  # 0 = INFO, 1 = WARN, 2 = ERROR, 3 = FATAL
logging.getLogger('tensorflow').setLevel(logging.ERROR)
import tensorflow as tf
from utilities.utils import load_param_file, set_logger, get_study_dirs
from utilities.input_functions import InputFunctions
from model.model_fn import model_fn
import numpy as np


def evaluate(params):
    # logging
    eval_logger = logging.getLogger()

    # no GPU distribution for evaluation (default)
    params.strategy = tf.distribute.get_strategy()

    # get eval dataset
    dataset_dir = os.path.join(params.model_dir, 'dataset')
    eval_data_dir = os.path.join(dataset_dir, 'eval')
    # first check for user specified data directories from function arguments
    if hasattr(params, "data_directories"):
        eval_logger.info("Loading data directories specified with command line argument...")
        study_dirs = params.data_directories
        eval_inputs = InputFunctions(params).get_dataset(data_dirs=study_dirs, mode="eval")
    # next check if there is an existing dataset directory containing a pre-generated dataset from generate_dataset.py
    else:
        if os.path.isdir(eval_data_dir):
            eval_logger.info(f"Loading existing evaluation dataset from {dataset_dir}")
            eval_inputs = tf.data.experimental.load(eval_data_dir)
        # else, try to determine eval dirs from the data directory
        else:
            study_dirs = get_study_dirs(params)["eval"]  # returns a dict of "train", "val", and "eval"
            # check for empty eval dirs
            if not study_dirs:
                eval_logger.error("No 'eval' directories found in the relevant study_dirs_list.yml file! "
                                  "These may need to be added manually depending on train/val/eval split. "
                                  "These may also be specified using the -d argument.")
                raise FileNotFoundError
            eval_inputs = InputFunctions(params).get_dataset(data_dirs=study_dirs, mode="eval")

    # load model from specified checkpoint
    eval_logger.info("Creating the model to resume checkpoint")
    # net_builder input layer uses train_dims, so set these to infer dims to allow different size inference
    params.train_dims = params.infer_dims
    model = model_fn(params)  # recreating model from scratch may be neccesary if custom loss function is used
    eval_logger.info(f"Loading model weights checkpoint file {params.checkpoint}")
    model.load_weights(params.checkpoint)

    # evaluation
    eval_logger.info(f"Evaluating model...")
    eval_results = model.evaluate(eval_inputs)
    eval_logger.info("Completed evaluation:")
    # print out metrics - note that the specified loss function is the first metric
    for i, metric in enumerate([params.loss] + params.metrics):
        eval_logger.info(f"    {metric} = {eval_results[i]:0.4f}")


# executed  as script
if __name__ == '__main__':
    # parse input arguments
    parser = argparse.ArgumentParser()
    parser.add_argument('-p', '--param_file', default=None, type=str,
                        help="Path to params.json")
    parser.add_argument('-l', '--logging', default=2, type=int, choices=[1, 2, 3, 4, 5],
                        help="Set logging level: 1=DEBUG, 2=INFO, 3=WARN, 4=ERROR, 5=CRITICAL")
    parser.add_argument('-c', '--checkpoint', default='last', type=str,
                        help="Can be 'best', 'last', or an hdf5 filename in the checkpoints subdirectory of model_dir")
    parser.add_argument('-f', '--force_cpu', default=False,
                        help="Disable GPU and force all computation to be done on CPU",
                        action='store_true')
    parser.add_argument('-d', '--directories', default=None, nargs="+", type=str,
                        help="Optionally specify directories to evaluate (overrides other eval data sources)")
    parser.add_argument('-x', '--overwrite', default=False,
                        help="Overwrite existing data.",
                        action='store_true')

    # Load the parameters from the experiment params.json file in model_dir
    args = parser.parse_args()
    assert args.param_file, "Must specify a parameter file using --param_file"
    assert os.path.isfile(args.param_file), f"No json configuration file found at {args.param_file}"

    # load params from param file
    my_params = load_param_file(args.param_file)

    # set global random seed for tensorflow operations
    tf.random.set_seed(my_params.random_state)

    # handle logging argument
    eval_dir = os.path.join(my_params.model_dir, 'evaluation')
    if not os.path.isdir(eval_dir):
        os.mkdir(eval_dir)
    log_path = os.path.join(eval_dir, 'eval.log')
    if os.path.isfile(log_path) and args.overwrite:
        os.remove(log_path)
    logger = set_logger(log_path, level=args.logging * 10)
    logger.info("Using model directory {}".format(my_params.model_dir))
    logger.info("Using TensorFlow version {}".format(tf.__version__))

    # handle force cpu argument
    if args.force_cpu:
        logger.info("Forcing CPU (GPU disabled)")
        os.environ["CUDA_VISIBLE_DEVICES"] = "-1"

    # handle checkpoint argument
    checkpoint_path = os.path.join(my_params.model_dir, 'checkpoints')
    checkpoints = glob(checkpoint_path + '/*.hdf5')
    if args.checkpoint == 'last':  # determine last by most recent file creation time
        my_params.checkpoint = max(checkpoints, key=os.path.getctime)
    elif args.checkpoint == 'best':  # determine best by minimum loss value in filename
        try:
            vals = [float(item[0:-5].split('_')[-1]) for item in checkpoints]
            my_params.checkpoint = checkpoints[np.argmin(vals)]
        except Exception:
            error = "Could not determine 'best' checkpoint based on checkpoint filenames. " \
                    "Use 'last' or pass a specific checkpoint filename to the checkpoint argument."
            logger.error(error)
            raise ValueError(error)
    elif os.path.isfile(os.path.join(my_params.model_dir, f"checkpoints/{args.checkpoint}.hdf5")):
        my_params.checkpoint = os.path.join(my_params.model_dir, f"checkpoints/{args.checkpoint}.hdf5")
    else:
        raise ValueError(f"Did not understand checkpoint value: {args.checkpoint}")

    # handle directories argument
    if args.directories:
        assert all([os.path.isdir(d) for d in args.directories]), "Not all specified directories exist!"
        my_params.data_directories = args.directories

    # do work
    evaluate(my_params)
