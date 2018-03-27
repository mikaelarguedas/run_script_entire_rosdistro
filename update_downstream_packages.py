#! /usr/bin/env python

from __future__ import print_function

import argparse
import copy
import os
import subprocess
import sys
import uuid
import yaml

from github import Github, GithubException, UnknownObjectException

_PY3 = sys.version_info[0] >= 3
if _PY3:
    from urllib.parse import urlparse
    from urllib.request import urlopen
    from urllib.error import URLError
else:
    from urlparse import urlparse
    from urllib2 import urlopen
    from urllib2 import URLError

# TODO if package list provided, clone only rdependant repos otherwise clone all
# TODO Error handling: raise on Fatal, skip on minor errors
# TODO Bail out for non git repos and repot the list
# TODO Bail out of non github repos
# TODO create fork if needed
# TODO push changes to upstream repo (or fork)
# TODO open PRs
# TODO provide options to skip parts of the process
# TODO add verbose mode
# TODO bail out if branch / PR already exists


def run_command(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True, cwd=None):
    if _PY3:
        result = subprocess.run(
            cmd, shell=shell, cwd=cwd,
            stdout=stdout, stderr=stderr
        )
        return result.stdout, result.stderr
    else:
        proc = subprocess.Popen(
            cmd, shell=shell, cwd=cwd, stdout=stdout, stderr=stderr, universal_newlines=True)
        std_out, error_out = proc.communicate()
        return std_out, error_out


def main(token, commit, rosdistro, pr_message, commit_message, branch_name, script, package_list):
    print(
        "1 clone all source repos registered for '%s'\n"
        "2 invoke '%s' in each of them\n"
        '3 show the diff if any\n'
        "4 commit the changes to a new branch '%s'\n"
        '5 fork repos (if needed) where there are changes\n'
        '6 push changes to fork\n'
        "7 open a PR with the following message '%s'\n" %
        (rosdistro, script, branch_name, pr_message)
    )
    ws_dir = os.path.join(os.sep, 'tmp', 'tmp' + rosdistro + uuid.uuid4().hex[:6].upper())
    repos_file_content = get_repos_list(rosdistro)
    file_path = save_repos_file(repos_file_content, ws_dir, rosdistro)
    rosinstall_repo_dict = get_repos_in_rosinstall_format(yaml.load(repos_file_content))
    print(file_path)
    print('cloning repositories')
    source_dir = clone_repositories(file_path)
    print('running script on packages')
    modified_pkgs_dict, modified_repos_list = run_script_on_repos(
        source_dir, script, package_list, show_diff=False)

    # diff on the entire workspace
    print_diff(source_dir)
    print('commiting changes')
    commit_changes(modified_pkgs_dict, commit_message, branch_name)
    gh = Github(token)
    # check for fork existence and create one if necessary
    print('check for repo access or forks')
    forks_to_create, existing_forks, repos_to_push_as_is = check_if_fork_needed(
        gh, modified_repos_list, rosinstall_repo_dict, pr_message, commit_message, branch_name)
    print('forks_to_create')
    print(forks_to_create)
    print('existing_forks')
    print(existing_forks)
    print('repos_to_push_as_is')
    print(repos_to_push_as_is)
    newly_forked_repositories = create_forks(gh, forks_to_create, commit)
    print('newly_forked_repositories')
    print(newly_forked_repositories)
    forked_repositories = dict(newly_forked_repositories, **existing_forks)
    print('dict of all forks')
    print(forked_repositories)
    remote_name = add_new_remotes(forked_repositories, source_dir)
    push_changes(
        branch_name, repos_to_push_as_is, forked_repositories, remote_name, source_dir, commit)
    repos_to_open_prs_from = copy.copy(repos_to_push_as_is)
    repos_to_open_prs_from += [
        forked_repositories[forked_repo] for forked_repo in forked_repositories.keys()]
    print('repos_to_open_prs_from')
    print(repos_to_open_prs_from)
    # open_pull_requests(gh, rosinstall_repo_dict, repos_to_open_prs_from, branch_name, commit)


def get_repos_in_rosinstall_format(root):
    repos = {}
    for i, item in enumerate(root):
        if len(item.keys()) != 1:
            raise RuntimeError('Input data is not valid format')
        repo = {'type': list(item.keys())[0]}
        attributes = list(item.values())[0]
        try:
            path = attributes['local-name']
        except AttributeError as e:
            continue
        try:
            repo['url'] = attributes['uri']
            if 'version' in attributes:
                repo['version'] = attributes['version']
        except AttributeError as e:
            continue
        repos[path] = repo
    return repos


def commit_changes(packages_dict, commit_message, branch_name):
    for package_name, package_path in packages_dict.items():
        branch_cmd = 'git rev-parse --abbrev-ref HEAD'
        output, error_out = run_command(branch_cmd, cwd=package_path)
        output = output.rstrip('\n')
        commit_cmd = ''
        if output != branch_name:
            commit_cmd = 'git checkout -b %s && ' % branch_name
        commit_cmd += 'git add . && git commit -m "[%s] %s"' % (
            package_name, commit_message)
        print("invoking '%s' in '%s'" % (commit_cmd, package_path))
        run_command(commit_cmd, cwd=package_path)


def check_if_fork_needed(
        gh, repo_dir_list, repo_dict, pr_message, commit_message, branch_name):
    gh_repo_dict = {}
    for key, value in repo_dict.items():
        url_paths = []
        o = urlparse(value['url'])
        url_paths = o.path.split('/')
        if len(url_paths) < 2:
            print('url parsing failed', file=sys.stderr)
            return
        base_org = url_paths[1]
        base_repo = url_paths[2][0:url_paths[2].rfind('.')]
        base_branch = value['version']
        gh_repo_dict[key] = {
            'base_org': base_org, 'base_repo': base_repo, 'base_branch': base_branch}
    # print(gh_repo_dict)
    ghuser = gh.get_user()
    head_org = ghuser.login  # The head org will always be gh user
    ghusername = ghuser.login
    ghuser_repos = ghuser.get_repos()
    print(head_org)
    print()
    print(repo_dir_list)
    print()
    forks_to_create = []
    repositories_to_push_without_forking = []
    existing_forks = {}
    skipped_repos = []
    # build list of modified repos
    for repo_path in repo_dir_list:
        repo = os.path.basename(repo_path)
        # print(gh_repo_dict[repo])
        base_org = gh_repo_dict[repo]['base_org']
        base_repo = gh_repo_dict[repo]['base_repo']
        base_branch = gh_repo_dict[repo]['base_branch']
        repo_full_name = base_org + '/' + base_repo
        ghuser_repos_full_names = [user_repo.full_name for user_repo in ghuser_repos]
        base_repo_object = gh.get_repo(repo_full_name)
        if repo_full_name in ghuser_repos_full_names:
            print("user '%s' has access to '%s'" % (ghusername, repo_full_name))
            repositories_to_push_without_forking.append(base_repo_object)
        else:
            try:
                base_repo_object.full_name
            except UnknownObjectException as e:
                print("'%s' is not a github repository, skipping...\n" % repo_full_name)
                skipped_repos.append(repo_full_name)
                continue
            fork_list = base_repo_object.get_forks()
            for fork in fork_list:
                if fork.full_name in ghuser_repos_full_names:
                    print("User has access to a fork! '%s'" % fork.full_name)
                    existing_forks[base_repo] = fork
                    break
            else:
                print("NO FORK FOUND, NEED TO CREATE A FORK OF '%s'\n" % repo_full_name)
                forks_to_create.append(repo_full_name)
    print('repositories to push to: %s\n' % repositories_to_push_without_forking)
    print('forks to create: %s\n' % forks_to_create)
    print('skipped repositories: %s\n' % skipped_repos)
    return forks_to_create, existing_forks, repositories_to_push_without_forking


def create_forks(gh, forks_to_create, commit):
    ghuser = gh.get_user()
    forked_repositories = {}
    for fork in forks_to_create:
        repo_to_fork = gh.get_repo(fork)
        cmd = "ghuser.create_fork('%s')" % repo_to_fork
        print("creating fork of: '%s'" % fork)
        # this is only for debugging
        forked_repositories[repo_to_fork.name] = repo_to_fork
        if commit:
            print('Here we will actually fork')
            # forked_repo = ghuser.create_fork(repo_to_fork)
            # # TODO use forked repo when done testing
            # forked_repositories[repo_to_fork.name] = forked_repo
    # returns the list of forked Github.Repository
    return forked_repositories


def add_new_remotes(forked_repositories, source_dir):
    remote_name = os.path.basename(os.path.dirname(source_dir))
    for repo_basename in forked_repositories.keys():
        cmd = 'git remote add %s %s' % (remote_name, forked_repositories[repo_basename].ssh_url)
        repo_path = os.path.join(source_dir, repo_basename)
        print(
            "adding new remote for forks: '%s' in '%s'" % (
                cmd, repo_path))
        output, error_out = run_command(cmd, cwd=repo_path)
    return remote_name


def push_changes(branch_name, repos_to_push, forked_repos_to_push, remote_name, source_dir, commit):
    print('fork_remote_name: %s' % remote_name)
    print(repos_to_push)
    for repo in repos_to_push:
        repo_path = os.path.join(source_dir, repo.name)
        cmd = 'git push origin %s' % branch_name
        print("pushing changes:\n invoking '%s' in '%s'" % (
            cmd, repo_path))
        if commit:
            print('Here we will actually push')
            output, error_out = run_command(cmd, cwd=repo_path)
    print(forked_repos_to_push)
    for repo_basename, repo in forked_repos_to_push.items():
        repo_path = os.path.join(source_dir, repo_basename)
        cmd = 'git push %s %s' % (remote_name, branch_name)
        print("pushing changes:\n invoking '%s' in '%s'" % (
            cmd, repo_path))
        if commit:
            print('Here we will actually push')
            output, error_out = run_command(cmd, cwd=repo_path)


def open_pull_requests(
        gh, rosinstall_repos_dict,
        repos_to_open_prs_from, branch_name, commit,
        pr_title, pr_body):
    for repo in repos_to_open_prs_from:
        base_branch = rosinstall_repos_dict[repo.name]['version']
        o = urlparse(rosinstall_repos_dict[repo.name]['url'])
        url_paths = o.path.split('/')
        if len(url_paths) < 2:
            print('url parsing failed', file=sys.stderr)
            return
        base_org = url_paths[1]
        base_repo = url_paths[2][0:url_paths[2].rfind('.')]
        base_repo_full_name = '/'.join([base_org, base_repo])
        cmd = "opening PR from repo:'%s' branch:'%s' to repo:'%s' branch :'%s'" % (
            repo.full_name, branch_name, base_repo_full_name, base_branch)
        print(cmd)
        if commit:
            print('Here we will actually open the PRs')
            print('running: repo.create_pull("%s", "%s", "%s", "%s", True)' % (
                pr_title, pr_body, base_branch, repo.name))
            # TODO confirm if this should be called on base repo or head repo
            # also conver full name on head_org:head_branch
            # repo.create_pull(
            #     title=pr_title,
            #     body=pr_body,
            #     base=base_branch,
            #     head=repo.full_name,
            #     maintainer_can_modify=True)


def print_diff(directory):
    cmd = 'vcs diff -s'

    run_command(cmd, cwd=directory, stdout=None, stderr=None)


def run_script_on_repos(directory, script, package_list, show_diff=False):
    # use rospack to find the packages that depend on the ones in package list
    # set the ros package path
    old_rpp = os.environ['ROS_PACKAGE_PATH']  # noqa
    os.environ['ROS_PACKAGE_PATH'] = directory
    dependent_packages = copy.copy(package_list)
    # print(len(package_list))
    for package in package_list:
        # print(package)
        cmd = 'rospack depends-on %s' % package
        print("invoking '%s' with RPP '%s'" % (cmd, os.environ['ROS_PACKAGE_PATH']))

        output, error_out = run_command(cmd)
        for pkg in output.split('\n'):
            if pkg == '':
                continue
            dependent_packages.append(pkg)
    nb_dependent_packages = len(dependent_packages)
    print(nb_dependent_packages)
    package_locations = {}
    # now find the location of the selected packages
    #  rospack find <pkg>
    for pkg in dependent_packages:
        cmd = 'rospack find %s' % pkg

        package_location, error_out = run_command(cmd)
        if not os.path.isdir(package_location.rstrip('\n')):
            print("package_location '%s' is not a directory" % package_location.rstrip('\n'))
        else:
            package_locations[pkg] = package_location.rstrip('\n')

    modified_pkgs = {}
    modified_repos = []
    for idx, pkg in enumerate(package_locations.keys()):
        pkg_path = package_locations[pkg]
        cmd = script
        print("package #%3d of %d: '%s'" % (idx + 1, nb_dependent_packages, pkg))
        # diff_res = None
        diff_cmd = 'git diff --shortstat'
        _, _ = run_command(cmd, cwd=pkg_path)
        diff_output, diff_err = run_command(diff_cmd, cwd=pkg_path)
        if diff_output != b'':
            print("adding '%s' to the list of modified_packages" % pkg)
            modified_pkgs[pkg] = pkg_path
            repo_basename = pkg_path.split('/')[4]
            if repo_basename not in modified_repos:
                modified_repos.append(repo_basename)
            if show_diff:
                print_diff(pkg_path)

    return modified_pkgs, modified_repos


def clone_repositories(file_path):
    repo_dir = os.path.dirname(file_path)
    src_dir = os.path.join(repo_dir, 'src')
    os.makedirs(src_dir)
    cmd = 'vcs import %s --input %s' % (src_dir, file_path)

    output, err_output = run_command(cmd)

    if err_output:
        print('cloning failed', file=sys.stderr)
        print(err_output, file=sys.stderr)
    return src_dir


def save_repos_file(repos_file_content, ws_dir, rosdistro):
    print(ws_dir)

    if os.path.isdir(ws_dir):
        os.removedirs(ws_dir)
    os.makedirs(ws_dir)
    repos_file_path = os.path.join(ws_dir, rosdistro + '_all.repos')
    with open(repos_file_path, 'wb') as f:
        f.write(repos_file_content)
    return repos_file_path


def get_repos_list(rosdistro):
    # cmd = 'rosinstall_generator ALL --rosdistro %s --deps --upstream-development' % rosdistro
    # cmd = 'rosinstall_generator moveit --rosdistro %s --deps --upstream-development' % rosdistro
    cmd = 'rosinstall_generator ros_base --rosdistro %s --deps --upstream-development' % rosdistro
    # cmd = 'rosinstall_generator rviz --rosdistro %s --deps --upstream-development' % rosdistro
    print('invoking: ' + cmd)
    output, err_output = run_command(cmd)
    if err_output:
        print(err_output)
    return output


if __name__ == '__main__':
    argparser = argparse.ArgumentParser()
    argparser.add_argument(
        '--token',
        help='Github token',)
    argparser.add_argument(
        '-r', '--rosdistro',
        type=str,
        required=True,
        choices=['indigo', 'kinetic', 'lunar'],
        help='ROS distribution to update',)
    argparser.add_argument(
        '-b', '--branch-name',
        type=str,
        required=True,
        help='name of the branch to create and push the changes to',)
    argparser.add_argument(
        '--pr-message',
        type=str,
        required=True,
        help='body of the resulting pull requests',)
    argparser.add_argument(
        '--commit-message',
        type=str,
        required=True,
        help='test used for the commit message',)
    argparser.add_argument(
        '--script',
        type=str,
        required=True,
        help='script / command to run on each repository',)
    argparser.add_argument(
        '--commit',
        action='store_true',
        default=False,
        help='actually modify upstream repo, we encourage to do a dry-run before using this flag',)
    argparser.add_argument(
        '--package-list',
        nargs='+',
        default=['class_loader'],
        help='The provided script will be ran only on these packages and '
             'packages that depend on it',)
    args = argparser.parse_args()

    main(
        token=args.token,
        commit=args.commit,
        rosdistro=args.rosdistro,
        pr_message=args.pr_message,
        commit_message=args.commit_message,
        branch_name=args.branch_name,
        script=args.script,
        package_list=args.package_list,
    )
