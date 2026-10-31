import logging
import multiprocessing
import os
import shutil
import subprocess
import sys
import tarfile
import urllib.error
import urllib.request

import ladybug as lb
import psutil

from serializer import _get_lbug_version

# Get the number of CPUs, try to use sched_getaffinity if available to account
# for Docker CPU limits
try:
    cpu_count = len(os.sched_getaffinity(0))
except AttributeError:
    cpu_count = multiprocessing.cpu_count()

# Use 90% of the available memory size as bm-size
# First try to read the memory limit from cgroup to account for Docker RAM
# limit, if not available use the total memory size
try:
    # cgroup v2
    max_memory = int(open("/sys/fs/cgroup/memory.max").readline().strip())
except FileNotFoundError:
    try:
        # cgroup v1
        max_memory = int(
            open("/sys/fs/cgroup/memory/memory.limit_in_bytes").readline().strip()
        )
    except FileNotFoundError:
        max_memory = psutil.virtual_memory().total

bm_size = int((max_memory / 1024**2) * 0.9)
base_dir = os.path.dirname(os.path.realpath(__file__))

# dataset registration
datasets = ["0.1", "0.3", "1", "3", "10", "30", "100"]

# Local directory where LSQB datasets are downloaded and cached
data_base_dir = os.path.join("/tmp", "lsqb-datasets")
serialized_base_dir = os.getenv("SERIALIZED_DIR")
timeout = os.getenv("TIMEOUT", None)
if timeout is not None:
    timeout = int(timeout)

benchmark_files = os.path.join(base_dir, "queries")

scale_factor = os.getenv("SCALE_FACTOR", "100")
if scale_factor not in datasets:
    logging.error(
        "SCALE_FACTOR %s is not a valid LSQB scale factor, must be one of %s",
        scale_factor,
        ", ".join(datasets),
    )
    sys.exit(1)
threads = os.getenv("NUM_THREADS", str(cpu_count))
threads = int(threads)

if serialized_base_dir is None:
    logging.error("SERIALIZED_DIR is not set, exiting...")
    sys.exit(1)

datasets_path = {
    "lsqb-sf0.1": os.path.join(data_base_dir, "social-network-sf0.1-projected-fk"),
    "lsqb-sf0.3": os.path.join(data_base_dir, "social-network-sf0.3-projected-fk"),
    "lsqb-sf1": os.path.join(data_base_dir, "social-network-sf1-projected-fk"),
    "lsqb-sf3": os.path.join(data_base_dir, "social-network-sf3-projected-fk"),
    "lsqb-sf10": os.path.join(data_base_dir, "social-network-sf10-projected-fk"),
    "lsqb-sf30": os.path.join(data_base_dir, "social-network-sf30-projected-fk"),
    "lsqb-sf100": os.path.join(data_base_dir, "social-network-sf100-projected-fk"),
}

serialized_graphs_path = {
    "lsqb-sf0.1": os.path.join(serialized_base_dir, "lsqb-sf0.1-serialized"),
    "lsqb-sf0.3": os.path.join(serialized_base_dir, "lsqb-sf0.3-serialized"),
    "lsqb-sf1": os.path.join(serialized_base_dir, "lsqb-sf1-serialized"),
    "lsqb-sf3": os.path.join(serialized_base_dir, "lsqb-sf3-serialized"),
    "lsqb-sf10": os.path.join(serialized_base_dir, "lsqb-sf10-serialized"),
    "lsqb-sf30": os.path.join(serialized_base_dir, "lsqb-sf30-serialized"),
    "lsqb-sf100": os.path.join(serialized_base_dir, "lsqb-sf100-serialized"),
}

benchmark_result_dir = os.path.join("/tmp", "benchmark_result")
shutil.rmtree(benchmark_result_dir, ignore_errors=True)
os.mkdir(benchmark_result_dir)


def get_dataset_url(scale_factor):
    return (
        f"https://datasets.ldbcouncil.org/lsqb/"
        f"social-network-sf{scale_factor}-projected-fk.tar.zst"
    )


def format_size(num_bytes):
    if num_bytes >= 1024**3:
        return f"{num_bytes / 1024**3:.1f} GiB"
    return f"{num_bytes / 1024**2:.1f} MiB"


def download_file(url, dest_path):
    logging.info("Downloading %s", url)
    tmp_path = dest_path + ".part"
    try:
        request = urllib.request.Request(
            url, headers={"User-Agent": "ladybug-lsqb-benchmark"}
        )
        with urllib.request.urlopen(request) as response, open(
            tmp_path, "wb"
        ) as f:
            total = int(response.headers.get("Content-Length") or 0)
            downloaded = 0
            last_reported = -1
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)
                if total > 0:
                    pct = int(downloaded * 100 // total)
                    if pct // 10 > last_reported:
                        last_reported = pct // 10
                        logging.info(
                            "Downloaded %d%% (%s of %s)",
                            pct,
                            format_size(downloaded),
                            format_size(total),
                        )
    except (urllib.error.URLError, OSError) as e:
        logging.error("Failed to download dataset from %s: %s", url, e)
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        sys.exit(1)
    os.replace(tmp_path, dest_path)
    logging.info("Download finished (%s)", format_size(downloaded))


def extract_archive(archive_path, dest_dir):
    logging.info("Extracting %s", os.path.basename(archive_path))
    # Python 3.14+ has built-in zstd support in tarfile
    if sys.version_info >= (3, 14):
        try:
            with tarfile.open(archive_path, "r:zst") as tf:
                tf.extractall(dest_dir, filter="data")
            return
        except (tarfile.TarError, LookupError, ValueError) as e:
            logging.warning(
                "tarfile zstd extraction failed (%s), falling back to tar CLI", e
            )
    # Fallback for older Python versions: use the tar CLI (GNU tar --zstd or bsdtar)
    subprocess.run(
        ["tar", "--zstd", "-xf", archive_path, "-C", dest_dir], check=True
    )


def download_dataset(dataset_name):
    dataset_path = datasets_path[dataset_name]
    if os.path.exists(os.path.join(dataset_path, "Person_knows_Person.csv")):
        logging.info("Dataset %s already downloaded, skipping", dataset_name)
        return dataset_path

    os.makedirs(data_base_dir, exist_ok=True)
    scale_factor = dataset_name[len("lsqb-sf"):]
    archive_path = os.path.join(
        data_base_dir, f"social-network-sf{scale_factor}-projected-fk.tar.zst"
    )

    try:
        download_file(get_dataset_url(scale_factor), archive_path)
        extract_archive(archive_path, data_base_dir)
    finally:
        if os.path.exists(archive_path):
            os.remove(archive_path)

    if not os.path.exists(
        os.path.join(dataset_path, "Person_knows_Person.csv")
    ):
        logging.error(
            "Dataset %s was not extracted correctly, expected data in %s",
            dataset_name,
            dataset_path,
        )
        sys.exit(1)

    logging.info(
        "Dataset %s downloaded and extracted to %s", dataset_name, dataset_path
    )
    return dataset_path


def serialize_dataset(dataset_name):
    dataset_path = datasets_path[dataset_name]
    serialized_graph_path = serialized_graphs_path[dataset_name]
    serializer_script = os.path.join(base_dir, "serializer.py")
    try:
        subprocess.run(
            [
                sys.executable,
                serializer_script,
                dataset_name,
                dataset_path,
                serialized_graph_path,
            ],
            check=True,
        )
    except subprocess.CalledProcessError as e:
        logging.error("Failed to serialize dataset: %s", e)
        sys.exit(1)


def run_query(conn, query_spec):
    # Monitor initial system usage
    initial_ram_usage = psutil.virtual_memory().used

    try:
        results = conn.execute(query_spec)
        if results.has_next():
            result = results.get_next()
            compiling_time = results.get_compiling_time()
            execution_time = results.get_execution_time()
    except RuntimeError:
        raise TimeoutError

    # Monitor final system usage
    final_ram_usage = psutil.virtual_memory().used
    ram_change = max(0, final_ram_usage - initial_ram_usage)

    return execution_time, compiling_time, ram_change, result


def run_lbug(sf, serialized_graph_path, num_threads):
    db = lb.Database(os.path.join(serialized_graph_path, "db.lbdb"))
    conn = lb.Connection(db, num_threads=num_threads)
    if timeout is not None:
        conn.set_query_timeout(timeout)

    with open(os.path.join(benchmark_result_dir, "results.csv"), "a+") as results_file:
        for i in range(1, 10):
            logging.info(f"Running query {i}")
            with open(
                os.path.join(base_dir, f"queries/q{i}.cypher"), "r"
            ) as query_file:
                query_spec = query_file.read()

                try:
                    # Warm up run
                    execution_time, compiling_time, memory, result = run_query(
                        conn, query_spec
                    )
                except TimeoutError:
                    logging.info(
                        "Query timed out after " + str(timeout / 1000) + " seconds"
                    )
                    results_file.write(
                        f"LbugDB\t{i}\t{num_threads} threads\t{sf}\tTimeout\tNA\tNA\t\n"
                    )
                    results_file.flush()
                    continue

                execution_times = [execution_time]
                compiling_times = [compiling_time]
                memories = [memory]

                num_trials = 3

                # Run the queries multiple times
                for _ in range(num_trials):
                    execution_time, compiling_time, memory, result = run_query(
                        conn, query_spec
                    )
                    execution_times.append(execution_time)
                    compiling_times.append(compiling_time)
                    memories.append(memory)

                # Get avg execution time
                max_execution_time = max(execution_times)
                execution_times.remove(max_execution_time)  # Remove the worst record
                execution_time = sum(execution_times) / len(execution_times)

                # Get avg compiling time
                max_compiling_time = max(compiling_times)
                compiling_times.remove(max_compiling_time)
                compiling_time = sum(compiling_times) / len(compiling_times)

                # Get avg memory
                filtered_memories = [x for x in memories if x != 0]
                if len(filtered_memories) == 0:
                    memory = 0
                else:
                    memory = sum(filtered_memories) / len(filtered_memories)

                # Log the result
                logging.info(f"Query execution successful. Output: {result[0]}")
                logging.info(f"Execution time (s): {execution_time / 1000:.4f}")
                logging.info(f"Compiling time (s): {compiling_time / 1000:.4f}")
                logging.info(f"Memory used: {memory / (1024 ** 3):.2f} GB\n")

                # Upload the result
                results_file.write(
                    f"LbugDB\t{i}\t{num_threads} threads\t{sf}\t{execution_time / 1000:.4f}\t{memory / (1024 ** 3):.2f} GB\t{result[0]}\n"
                )
                results_file.flush()


if __name__ == "__main__":
    dataset_name = "lsqb-sf" + scale_factor
    dataset_path = datasets_path[dataset_name]

    logging.basicConfig(
        level=logging.INFO,
        handlers=[
            logging.FileHandler(os.path.join("lsqb.log")),
            logging.StreamHandler(),
        ],
    )
    logging.info("Running benchmark for scale factor %s", scale_factor)
    logging.info("Database version: %s", _get_lbug_version())
    logging.info("CPU cores: %d", cpu_count)
    logging.info("Using %s threads", threads)
    logging.info("Total memory: %d GiB", max_memory / 1024**3)
    logging.info("Buffer manager size: %d MiB", bm_size)
    if timeout is not None:
        logging.info("Query timeout: %d ms", timeout)
    else:
        logging.info("Query timeout is not set, queries will run until completion")

    # download dataset if needed, then serialize it
    download_dataset(dataset_name)
    serialize_dataset(dataset_name)

    logging.info("Running benchmark...")
    run_lbug(scale_factor, serialized_graphs_path[dataset_name], threads)
    logging.info("Benchmark finished")
