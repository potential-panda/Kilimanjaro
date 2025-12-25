import argparse
import signal
import sys
from dataclasses import dataclass
from logging import getLogger
from pathlib import Path

from flask import Flask, jsonify, request
from flask_cors import CORS
from qate.env import sys_env
from qate.env.env import Env
from qate.env.env_name import EnvName

from kilimanjaro.data_access import JobDataAccess

LOG = getLogger(__name__)
PORT = 8766
JOB_DIR = Path(sys_env.ENV_DIR) / EnvName.KILIMANJARO_API.name / "jobs"
RESULTS_DIR = Path(sys_env.ENV_DIR) / EnvName.KILIMANJARO_API.name / "results"
WWW_DIR = Path(__file__).resolve().parent.parent / "web" / "public"


@dataclass
class Config:
    port: int = PORT
    job_dir: str = str(JOB_DIR)
    results_dir: str = str(RESULTS_DIR)
    extra_results_dirs: list = None

    def __post_init__(self):
        if self.extra_results_dirs is None:
            self.extra_results_dirs = []


# Note: Locking is handled in JobDataAccess layer


def extract_dir_name(dir_path):
    """Extract the directory name from a path like /.../$a/$b - returns $a"""
    path = Path(dir_path)
    # Get parent directory name ($a from /.../$a/$b)
    return path.parent.name


def get_results_from_dir(dir_path):
    """Get all result JSON files from a specific directory"""
    import json

    results_dir = Path(dir_path)
    if not results_dir.exists():
        return []

    run_logs = []
    try:
        for result_file in results_dir.glob("*.json"):
            try:
                with open(result_file, "r") as f:
                    run_log_data = json.load(f)
                run_logs.append(run_log_data)
            except Exception as e:
                LOG.warning(f"Error reading result file {result_file}: {e}")
    except Exception as e:
        LOG.error(f"Error reading results directory {dir_path}: {e}")

    # Sort by start_ts if available, newest first
    run_logs.sort(key=lambda x: x.get("start_ts", 0), reverse=True)
    return run_logs


def convert_string_to_number(value):
    """Convert string to appropriate numeric type if possible"""
    if not isinstance(value, str):
        return value

    # Try to convert to int first
    try:
        if "." not in value and "e" not in value.lower():
            return int(value)
    except ValueError:
        pass

    # Try to convert to float
    try:
        return float(value)
    except ValueError:
        pass

    # Return original string if conversion fails
    return value


def process_job_data(job_data):
    """Process job data to convert string numbers to proper types"""
    if not job_data:
        return job_data

    # Process config values
    if "config" in job_data and isinstance(job_data["config"], dict):
        for key, value in job_data["config"].items():
            job_data["config"][key] = convert_string_to_number(value)

    # Process param_grid values
    if "param_grid" in job_data and isinstance(job_data["param_grid"], list):
        for param in job_data["param_grid"]:
            if isinstance(param, dict):
                # Convert default_value
                if "default_value" in param:
                    param["default_value"] = convert_string_to_number(param["default_value"])

                # Convert factor
                if "factor" in param:
                    param["factor"] = convert_string_to_number(param["factor"])

                # Convert values array
                if "values" in param and isinstance(param["values"], list):
                    param["values"] = [convert_string_to_number(v) for v in param["values"]]

    return job_data


def create_app(config: Config = None):
    app = Flask(__name__, static_folder=WWW_DIR, static_url_path="/html")
    CORS(app, origins=["http://localhost:8080"])

    if config is None:
        config = Config()

    job_data_access = JobDataAccess(config.job_dir, config.results_dir)

    @app.route("/job/new", methods=["POST"])
    def create_job():
        try:
            job_data = request.get_json()
            if not job_data:
                return jsonify({"error": "No JSON data provided"}), 400

            # Process job data to convert string numbers to proper types
            job_data = process_job_data(job_data)

            job_id = job_data_access.create_job(job_data)
            return jsonify({"job_id": job_id}), 201

        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/job/delete/<job_id>", methods=["DELETE"])
    def delete_job(job_id):
        try:
            if job_data_access.delete_job(job_id):
                return "", 204
            else:
                return jsonify({"error": "Job not found"}), 404

        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/job/acquire", methods=["POST"])
    def acquire_job():
        try:
            job_data = job_data_access.acquire_job()
            if job_data:
                return jsonify(job_data), 200
            else:
                return jsonify({}), 200

        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/job/list", methods=["GET"])
    def list_jobs():
        try:
            jobs = job_data_access.list_jobs()
            return jsonify(jobs), 200

        except Exception as e:
            LOG.exception(e)
            return jsonify({"error": str(e)}), 500

    @app.route("/job/update/<job_id>", methods=["POST"])
    def update_job(job_id):
        try:
            update_data = request.get_json()
            if not update_data:
                return jsonify({"error": "No JSON data provided"}), 400

            if job_data_access.update_job(job_id, update_data):
                return "", 204
            else:
                return jsonify({"error": "Job not found"}), 404

        except Exception as e:
            LOG.exception(e)
            return jsonify({"error": str(e)}), 500

    @app.route("/job/<job_id>", methods=["GET"])
    def get_job(job_id):
        try:
            job_data = job_data_access.get_job(job_id)
            if job_data:
                return jsonify(job_data), 200
            else:
                return jsonify({"error": "Job not found"}), 404

        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/result/dirs", methods=["GET"])
    def list_result_dirs():
        """Return list of available result directory names"""
        try:
            dirs = []
            # Add default results dir
            dirs.append({"name": "default", "path": config.results_dir})

            # Add extra results dirs
            for dir_path in config.extra_results_dirs:
                name = extract_dir_name(dir_path)
                dirs.append({"name": name, "path": dir_path})

            return jsonify(dirs), 200

        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/result/list", methods=["GET"])
    def list_results():
        try:
            # Get optional 'dir' parameter
            dir_name = request.args.get("dir", "default")

            # If default, use the job_data_access
            if dir_name == "default":
                run_logs = job_data_access.get_all_run_logs()
            else:
                # Find the matching extra results dir
                matching_dir = None
                for dir_path in config.extra_results_dirs:
                    if extract_dir_name(dir_path) == dir_name:
                        matching_dir = dir_path
                        break

                if matching_dir:
                    run_logs = get_results_from_dir(matching_dir)
                else:
                    return jsonify({"error": f"Directory '{dir_name}' not found"}), 404

            return jsonify(run_logs), 200

        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/result/<bucket_name>", methods=["DELETE"])
    def delete_result(bucket_name):
        try:
            # Extract env_name from bucket_name (format: FirstName_LastName_xxxxx)
            parts = bucket_name.split("_")
            if len(parts) >= 2:
                env_name = f"{parts[0]}_{parts[1]}"
            else:
                env_name = None

            # If env_name is BENJAMIN_TATUM, try to delete from default results_dir
            if env_name == "BENJAMIN_TATUM":
                result_file_path = Path(config.results_dir) / f"{bucket_name}.json"
                if result_file_path.exists():
                    try:
                        result_file_path.unlink()
                        return "", 204
                    except Exception as e:
                        LOG.warning(f"Error deleting result file {bucket_name}: {e}")
                        return jsonify({"error": str(e)}), 500

            # If not returned yet, try to match env_name in extra_results_dirs
            if env_name:
                for dir_path in config.extra_results_dirs:
                    # Check if this extra_results_dir contains the env_name
                    if env_name in dir_path:
                        result_file_path = Path(dir_path) / f"{bucket_name}.json"
                        if result_file_path.exists():
                            try:
                                result_file_path.unlink()
                                return "", 204
                            except Exception as e:
                                LOG.warning(f"Error deleting result file {bucket_name}: {e}")
                                return jsonify({"error": str(e)}), 500

            # File not found in any location
            return jsonify({"error": "Result not found"}), 404

        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/health", methods=["GET"])
    def health():
        return jsonify({"status": "healthy"}), 200

    return app


def main():
    env = Env(sys_env.ENV_DIR, EnvName.KILIMANJARO_API)
    env.load()

    def signal_handler(signum, frame):
        """Handle shutdown signals gracefully"""
        print(f"\nReceived signal {signum}, shutting down gracefully...")
        env.leave()
        sys.exit(0)

    # Register signal handlers for graceful shutdown
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    try:
        env.enter(daemon=True)
        config = env.get("config")

        app = create_app(config)

        print(f"Starting Kilimanjaro API server on port {config.port}")
        print(f"Job directory: {config.job_dir}")
        print(f"Results directory: {config.results_dir}")
        if config.extra_results_dirs:
            print("Extra results directories:")
            for dir_path in config.extra_results_dirs:
                print(f"  - {extract_dir_name(dir_path)}: {dir_path}")
        app.run(host="0.0.0.0", port=config.port, threaded=True)
    finally:
        env.leave()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--setup", action="store_true")
    args = parser.parse_args()

    if args.setup:
        Env.setup_env(
            sys_env.ENV_DIR,
            EnvName.KILIMANJARO_API,
            {
                "config": Config(),
            },
        )
        exit(0)

    main()
