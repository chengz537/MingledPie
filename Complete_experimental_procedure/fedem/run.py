import os
import pandas as pd
from tqdm import tqdm
from torch.utils.tensorboard import SummaryWriter
from utils.utils import *
from utils.constants import *
from utils.args import *


def init_clients(args_, root_path, logs_dir):
    """
    initialize clients from data folders
    :param args_:
    :param root_path: path to directory containing data folders
    :param logs_dir: path to logs root
    :return: List[Client]
    """
    print("===> Building data iterators..")
    train_iterators, val_iterators, test_iterators = \
        get_loaders(
            type_=LOADER_TYPE[args_.experiment],
            root_path=root_path,
            batch_size=args_.bz,
            is_validation=args_.validation
        )

    print("===> Initializing clients..")
    clients_ = []
    for task_id, (train_iterator, val_iterator, test_iterator) in \
            enumerate(tqdm(zip(train_iterators, val_iterators, test_iterators), total=len(train_iterators))):

        if train_iterator is None or test_iterator is None:
            continue

        learners_ensemble = \
            get_learners_ensemble(
                n_learners=args_.n_learners,
                name=args_.experiment,
                device=args_.device,
                optimizer_name=args_.optimizer,
                scheduler_name=args_.lr_scheduler,
                initial_lr=args_.lr,
                input_dim=args_.input_dimension,
                output_dim=args_.output_dimension,
                n_rounds=args_.n_rounds,
                seed=args_.seed,
                mu=args_.mu
            )

        logs_path = os.path.join(logs_dir, "task_{}".format(task_id))
        os.makedirs(logs_path, exist_ok=True)
        logger = SummaryWriter(logs_path)

        client = get_client(
            client_type=CLIENT_TYPE[args_.method],
            learners_ensemble=learners_ensemble,
            q=args_.q,
            train_iterator=train_iterator,
            val_iterator=val_iterator,
            test_iterator=test_iterator,
            logger=logger,
            local_steps=args_.local_steps,
            tune_locally=args_.locally_tune_clients
        )

        clients_.append(client)

    return clients_


def run_experiment(args_):
    torch.manual_seed(args_.seed)

    data_dir = get_data_dir(args_.experiment)

    if "logs_dir" in args_:
        logs_dir = args_.logs_dir
    else:
        logs_dir = os.path.join("logs", args_to_string(args_))

    print("==> Clients initialization..")
    clients = init_clients(args_, root_path=os.path.join(data_dir, "train"), logs_dir=os.path.join(logs_dir, "train"))

    print("==> Test Clients initialization..")
    test_clients = init_clients(args_, root_path=os.path.join(data_dir, "test"),
                                logs_dir=os.path.join(logs_dir, "test"))

    logs_path = os.path.join(logs_dir, "train", "global")
    os.makedirs(logs_path, exist_ok=True)
    global_train_logger = SummaryWriter(logs_path)

    logs_path = os.path.join(logs_dir, "test", "global")
    os.makedirs(logs_path, exist_ok=True)
    global_test_logger = SummaryWriter(logs_path)

    global_learners_ensemble = \
        get_learners_ensemble(
            n_learners=args_.n_learners,
            name=args_.experiment,
            device=args_.device,
            optimizer_name=args_.optimizer,
            scheduler_name=args_.lr_scheduler,
            initial_lr=args_.lr,
            input_dim=args_.input_dimension,
            output_dim=args_.output_dimension,
            n_rounds=args_.n_rounds,
            seed=args_.seed,
            mu=args_.mu
        )

    if args_.decentralized:
        aggregator_type = 'decentralized'
    else:
        aggregator_type = AGGREGATOR_TYPE[args_.method]

    aggregator = \
        get_aggregator(
            aggregator_type=aggregator_type,
            clients=clients,
            global_learners_ensemble=global_learners_ensemble,
            lr_lambda=args_.lr_lambda,
            lr=args_.lr,
            q=args_.q,
            mu=args_.mu,
            communication_probability=args_.communication_probability,
            sampling_rate=args_.sampling_rate,
            log_freq=args_.log_freq,
            global_train_logger=global_train_logger,
            global_test_logger=global_test_logger,
            test_clients=test_clients,
            verbose=args_.verbose,
            seed=args_.seed
        )

    print("Training..")
    pbar = tqdm(total=args_.n_rounds)
    current_round = 0
    results = []

    while current_round <= args_.n_rounds:
        aggregator.mix()

        if aggregator.c_round != current_round:
            # Record test accuracy to results
            test_accuracy = calculate_test_accuracy(aggregator, test_clients)
            if test_accuracy is not None:
                round_result = {
                    "round": current_round,
                    "test_accuracy": test_accuracy,
                }
                results.append(round_result)

                # Update Excel file
                df = pd.DataFrame(results)
                excel_file = os.path.join(logs_dir, 'test_accuracy.xlsx')
                df.to_excel(excel_file, index=False)
                print(f"Round {current_round}: Test Accuracy = {test_accuracy}")
            pbar.update(1)
            current_round = aggregator.c_round

    if "save_dir" in args_:
        save_dir = os.path.join(args_.save_dir)
        os.makedirs(save_dir, exist_ok=True)
        aggregator.save_state(save_dir)

    return results

def calculate_test_accuracy(aggregator, test_clients):
    total_accuracy = 0.0
    num_clients = len(test_clients)

    for client in test_clients:
        # Perform evaluation on each client
        client_eval_result = client.evaluate()
        total_accuracy += client_eval_result['accuracy']

    # Calculate average accuracy across all clients
    if num_clients > 0:
        return total_accuracy / num_clients
    else:
        return None

if __name__ == "__main__":
    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = False

    args = parse_args()
    results = run_experiment(args)
