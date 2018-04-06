#!/bin/bash
docker run --rm \
  -v $HOME/.ssh:/root/.ssh:ro \
  -v $HOME/.gitconfig:/root/.gitconfig:ro \
  -v /home/mikael/work/run_script_entire_rosdistro/update_downstream_packages.py:/root/update_downstream_packages.py \
  -ti update_downstream
