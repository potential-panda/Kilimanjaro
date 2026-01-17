#!/usr/bin/env bash

cd -- "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

git pull
pip uninstall potential-panda-core -y
pip install .

echo "Release done."
