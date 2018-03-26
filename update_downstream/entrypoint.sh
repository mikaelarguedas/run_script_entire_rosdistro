#!/bin/bash
set -e

source $HOME/.bashrc

# setup ros
. /opt/ros/kinetic/setup.bash
exec "$@"
