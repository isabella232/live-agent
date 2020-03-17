Live Agent
----------

Coordinates the execution of processes which interact with Intelie Live and simplifies their deployment.

Processes are implemented inside modules, segmented by its goal. In order to start a new module, run the command `add-agent-module`.

Each module can have:
- `datasources`: Process which generates and send events to live
- `monitors`: Processes which respond to events generated by queries
- `logic_adapters`: Classes which handle messages received by the chatbot

The set of active modules (among other things) is defined using a settings file.

A default settings file (`settings.json`) was created with this agent. By default, the only enabled process is `live-agent`'s own `chatbot`, whith its default logic_adapters.
When you implement a new module you will have to add it to the settings file.


## Development

`live-agent` uses [black](https://github.com/psf/black) and [pre-commit](https://pre-commit.com/), and recomments you do the same. The recommended development dependencies are defined at `dev-requirements.txt`.
In order to install the dev dependencies and initialize `pre-commit`, use the following commands:

```shell
$ pip install -r dev-requirements.txt
$ pre-commit install
$ pre-commit run --all-files
```

### Project setup:

Requires python 3.6 or newer

```shell
# 1- Create a virtualenv

# 2- Activate the virtualenv

# 3- Install live-agent (you probably should use a requirements.txt file to manage your dependencies)
$ pip install live-agent

# 4- Bootstrap a new agent
$ create-agent

# 5- Manually update the settings file. After each change, validate it
$ check_live_features --settings=settings.json

# 6- Add agent modules for your specific requirements
$ add-agent-module <module-name>

# 7- Implement the features you need on your modules and add them to settings.json

# 8- Execute the agent
$ agent-control console --settings=settings.json
```

### Reading logs

This project uses `eliot` for logging. Eliot generates log messages as json objects,
which can be parsed by tools like `eliot-tree` and `eliot-prettyprint` or sent to Intelie Live.

The log file is stored at `/var/log/live-agent.log` by default. Make sure the user which will start the agent can write to this file.
The log messages are also sent to live, using the event_type `dda_log` by default.

```shell
# Reading the log with eliot-prettyprint
$ tail -f /var/log/live-agent.log | eliot-prettyprint

# Reading the log with eliot-tree (extra dependency, already on requirements.txt)
$ eliot-tree -l 0 /var/log/live-agent.log
```

### Building releases

In order to generate an installable package you will need to use `docker`.

- Install docker (check the documentation for your system: <https://docs.docker.com/install/>)
- Add your user to the group `docker`: `$ usermod -aG docker <username>`.
- Log off and log on again for the group to be recognized. (or you can simply `$ su - <username>` on your terminal)

The packager requires packages `fabric` and `virtualenv`. It will generate a rpm file for installation on the target system.

```shell
$ tools/package.sh c7
```

- `c7`: Build for centos7 and derivates (redhat 7, amazon linux 2, etc)


#### Testing the built packages

(As of now the testing is entirely manual)

##### In a container:

```shell
$ tools/test-envs/run_centos_container.sh 7

# Build dir will be available at /packages, so you can install and test
```

##### In a VM:

This allows to a more complete test, including running the app as a service

- Install VirtualBox and Vagrant (https://www.vagrantup.com/downloads.html)

```shell
# cd to `tools/test-envs/RedHat7`
$ cd tools/test-envs/RedHat7

# Starting VM:
$ vagrant up

# Connecting to the machine:
$ `vagrant ssh`

# To transfer files, copy to/from the `transf` subdirectory,
# it is automatically mapped as `/transf` at the test VM

# Stopping:

$ vagrant halt    # Stop
$ vagrant destroy # Completely erase the machine
```


#### Installing on the target system

```shell
$ rpm -Uvh <rpmfile>
```