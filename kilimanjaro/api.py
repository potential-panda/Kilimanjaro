import argparse
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


# Note: Locking is handled in JobDataAccess layer


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

    @app.route("/result/list", methods=["GET"])
    def list_results():
        try:
            run_logs = job_data_access.get_all_run_logs()
            return jsonify(run_logs), 200

        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/result/<bucket_name>", methods=["DELETE"])
    def delete_result(bucket_name):
        try:
            if job_data_access.delete_result(bucket_name):
                return "", 204
            else:
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
    try:
        env.enter()
        config = env.get("config")

        app = create_app(config)

        print(f"Starting Kilimanjaro API server on port {config.port}")
        print(f"Job directory: {config.job_dir}")
        print(f"Results directory: {config.results_dir}")
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
