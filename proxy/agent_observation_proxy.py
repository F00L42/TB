"""Run with: python -m proxy.agent_observation_proxy --config proxy/observation.example.yaml"""
from __future__ import annotations

import argparse
import logging

from proxy.observation.config import load_config
from proxy.observation.transport import ObservationProxy


def create_app(config_path=None, **kwargs):
    return ObservationProxy(load_config(config_path, kwargs.pop("overrides", None)), **kwargs)


def main():
    parser = argparse.ArgumentParser(description="Passive agent observation proxy")
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    config = load_config(args.config)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    # httpx INFO logs include request URLs, which may contain credentials in query strings.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    import uvicorn
    uvicorn.run(ObservationProxy(config), **config["listen"], lifespan="on", access_log=False,
                timeout_graceful_shutdown=30)


if __name__ == "__main__":
    main()
