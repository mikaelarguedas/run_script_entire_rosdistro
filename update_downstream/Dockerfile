FROM ubuntu:xenial

RUN apt-get -qq update && \
    apt-get -qq install locales -y

RUN locale-gen en_US en_US.UTF-8 && \
    update-locale LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8

ENV LANG en_US.UTF-8

RUN apt-key adv --keyserver hkp://keyserver.ubuntu.com:80 --recv-keys 421C365BD9FF1F717815A3895523BAEEB01FA116

RUN echo "deb http://packages.ros.org/ros/ubuntu xenial main" > /etc/apt/sources.list.d/ros-latest.list

# upgrade distro
RUN apt-get -qq update && \
    apt-get -qq dist-upgrade -y

RUN apt-get -qq update && apt-get install -y \
    build-essential \
    git \
    python-pip \
    python-rosdep \
    python-rosinstall \
    python-rosinstall-generator \
    python-vcstool \
    python-wstool \
    ros-kinetic-rosbash \
    ros-kinetic-rospack \
    vim \
    wget

RUN apt-get -qq update && \
    apt-get install -y ros-kinetic-ros-core

RUN pip install pygithub

WORKDIR /root/

RUN wget https://raw.githubusercontent.com/ros/class_loader/indigo-devel/scripts/class_loader_headers_update.py && \
    chmod +x class_loader_headers_update.py
RUN wget https://raw.githubusercontent.com/ros/pluginlib/kinetic-devel/scripts/pluginlib_headers_migration.py && \
    chmod +x pluginlib_headers_migration.py

RUN wget https://raw.githubusercontent.com/mikaelarguedas/run_script_entire_rosdistro/master/update_downstream_packages.py
RUN rosdep init && rosdep update

# setup entrypoint
COPY ./entrypoint.sh /

ENTRYPOINT ["/entrypoint.sh"]
CMD ["bash"]
