#!/bin/zsh
cd "$(dirname "$0")"
clear
echo "Starting ATEM Mock..."
echo
/usr/bin/python3 atem_mock.py
