#!/bin/bash
# 运行 DataBoard 后端测试
cd "$(dirname "$0")"
PYTHONWARNINGS=ignore /home/gaofeng/dataBoard/.venv/bin/python -c "
import sys
sys.path = [p for p in sys.path if '/opt/ros/' not in p]
import pytest
sys.exit(pytest.main(['tests/', '-v'] + sys.argv[1:]))
" "$@"
