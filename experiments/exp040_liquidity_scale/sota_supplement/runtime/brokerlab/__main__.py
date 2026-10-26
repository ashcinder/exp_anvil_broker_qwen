"""python -m brokerlab 的入口：转调 cli.main()，并以其返回值作退出码。"""
import sys

from .cli import main

sys.exit(main())
