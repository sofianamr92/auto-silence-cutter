# -*- coding: utf-8 -*-
import os

APP_NAME = "AUTO SILENCE CUTTER"
APP_VERSION = "2.4.0"
DEFAULT_SILENCE_THRESHOLD_DB = -38.0
DEFAULT_MIN_SILENCE_DURATION = 0.60
DEFAULT_PAD_BEFORE = 0.15
DEFAULT_PAD_AFTER = 0.18

APP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CACHE_DIR = os.path.join(APP_DIR, ".cache")
os.makedirs(CACHE_DIR, exist_ok=True)
