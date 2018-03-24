#! /usr/bin/env python

from __future__ import print_function

import argparse
import copy
import os
import subprocess
import sys
import uuid
import yaml

from github import Github, GithubException, GithubObject

_py3 = sys.version_info[0] >= 3
if _py3:
    from urllib.parse import urlparse
else:
    from urlparse import urlparse

# TODO if package list provided, clone only rdependant repos otherwise clone all
# TODO Error handling: raise on Fatal, skip on minor errors
# TODO Bail out for non git repos and repot the list
# TODO create fork if needed
# TODO push changes to upstream repo (or fork)
# TODO open PRs
# TODO provide options to skip parts of the process
# TODO add verbose mode
# TODO bail out if branch / PR already exists

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
    repos_file_content = get_repos_list(rosdistro)
    file_path = save_repos_file(repos_file_content, rosdistro)
    print(file_path)
    source_dir = clone_repositories(file_path)
    package_dir_list = run_script_on_repos(source_dir, script, package_list, show_diff=False)

    print(package_dir_list)
    # diff on the entire workspace
    print_diff(source_dir)
    commit_changes(package_dir_list, commit_message, branch_name)
    # if True:
    if False:
        gh = Github(token)
        # check for fork existence and create one if necessary
        create_fork_if_needed(
            gh, package_dir_list, repos_file_content, pr_message, commit_message, branch_name)


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


def commit_changes(package_dir_list, commit_message, branch_name):
    for package_path in package_dir_list:
        # cmd = 'cd %s && git checkout -b %s && git add . && git commit -m "[%s]%s"' % (
        #     package_path, branch_name, os.path.basename(package_path), commit_message)
        cmd = 'git rev-parse --abbrev-ref HEAD'
        if _py3:
            result = subprocess.run(
                cmd, shell=True, cwd=package_path, 
                stdout=subprocess.PIPE  # , stderr=subprocess.PIPE
            )
            output = result.stdout
        else:
            proc = subprocess.Popen(cmd, shell=True, cwd=package_path, stdout=subprocess.PIPE)
            output, stderr_output = proc.communicate()
        output = output.rstrip('\n')
        print("'%s'" % output)
        cmd = 'git add . && git commit -m "[%s] %s"' % (
            os.path.basename(package_path), commit_message)
        if output != branch_name:
            cmd = 'git checkout -b %s && ' % branch_name + cmd
        # cmd = 'git checkout -b %s && git add . && git commit -m "[%s]%s"' % (
        #     branch_name, os.path.basename(package_path), commit_message)
        print("invoking '%s' in '%s'" % (cmd, package_path))
        if _py3:
            subprocess.run(
                cmd, shell=True, cwd=package_path, 
                stdout=subprocess.PIPE  # , stderr=subprocess.PIPE
            )
        else:
            subprocess.call(
                cmd, shell=True, cwd=package_path, 
                stdout=subprocess.PIPE  # , stderr=subprocess.PIPE
            )


def create_fork_if_needed(gh, repo_dir_list, repos_file_content, pr_message, commit_message, branch_name):
    root = yaml.load(repos_file_content)
    repo_dict = get_repos_in_rosinstall_format(root)
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
    for repo_path in repo_dir_list:
        repo = os.path.basename(repo_path)
        print(gh_repo_dict[repo])
        base_org = gh_repo_dict[repo]['base_org']
        base_repo = gh_repo_dict[repo]['base_repo']
        base_branch = gh_repo_dict[repo]['base_branch']
        for user_repo in ghuser_repos:
            if base_org + '/' + base_repo == user_repo.full_name:
                print("user '%s' has access to '%s'" % (ghusername, base_org + '/' + base_repo))
                break
        else:
            print("user '%s' does not have access to '%s'\nFork required\n" % (ghusername, base_org + '/' + base_repo))
        # if head_org == base_org:
        #     # repo is on the user organization, no need to fork
        #     head_repo = gh.get_repo(base_org, base_repo)
        # else:
        #     try:
        #         repo_forks = gh.list_forks(base_org, base_repo)
        #         user_forks = [r for r in repo_forks if r.get('owner', {}).get('login', '') == username]
        #         # github allows only 1 fork per org as far as I know. We just take the first one.
        #         head_repo = user_forks[0] if user_forks else None

        #     except GithubException as exc:
        #         pass  # 404 or unauthorized, but unauthorized should have been caught above
        # print(head_repo)
        # print(head_org)
        # print(repo_forks)
        # print(user_forks)
        # # check if there's a diff


def print_diff(directory):
    # cmd = 'cd %s && vcs diff -s' % directory
    cmd = 'vcs diff -s'
    if _py3:
        subprocess.run(cmd, cwd=directory, shell=True)
    else:
        subprocess.call(cmd, cwd=directory, shell=True)


def run_script_on_repos(directory, script, package_list, show_diff=False):
    repo_dir_list = os.listdir(directory)
    modified_repos = []
    # use rospack to find the packages that depend on the ones in package list 
    # set the ros package path
    old_rpp = os.environ['ROS_PACKAGE_PATH']
    os.environ['ROS_PACKAGE_PATH'] = directory
    dependent_packages = copy.copy(package_list)
    print(len(package_list))
    for package in package_list:
        print(package)
        cmd = 'rospack depends-on %s' % package
        print("invoking '%s' with RPP '%s'" % (cmd, os.environ['ROS_PACKAGE_PATH']))
        if _py3:
            subprocess.run(cmd, shell=True, stdout=subprocess.PIPE)
            depends_res = subprocess.run(diff_cmd, shell=True, stdout=subprocess.PIPE)
            tmpdepends_list = depends_res.stdout
        else:
            # subprocess.call(cmd, shell=True, stdout=subprocess.DEVNULL)
            proc = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE)
            tmpdepends_list, stderr_output = proc.communicate()
        # print('tmpdepends_list: %s' % tmpdepends_list)
        # print(tmpdepends_list.split('\n'))
        print(len(tmpdepends_list.split('\n')))
        for pkg in tmpdepends_list.split('\n'):
            if pkg == '':
                continue
            # print(pkg)
            dependent_packages.append(pkg)
    print(dependent_packages)
    nb_dependent_packages = len(dependent_packages)
    package_locations = []
    # now find the location of the selected packages
    #  rospack find <pkg>
    for pkg in dependent_packages:
        cmd = 'rospack find %s' % pkg
        if _py3:
            depends_res = subprocess.run(diff_cmd, shell=True, stdout=subprocess.PIPE)
            package_location = depends_res.stdout
        else:
            # subprocess.call(cmd, shell=True, stdout=subprocess.DEVNULL)
            proc = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE)
            package_location, _ = proc.communicate()
        # print(package_location.rstrip('\n'))
        if not os.path.isdir(package_location.rstrip('\n')):
            print("package_location '%s' is not a directory" % package_location.rstrip('\n'))
        else:
            package_locations.append(package_location.rstrip('\n'))
    # print(package_locations)

    for idx, repo_path in enumerate(package_locations):
    #     repo_path = os.path.join(directory, repo)
        # cmd = 'cd %s && %s' % (repo_path, script)
        cmd = script
        print("repo #%d of %d: '%s'" % (idx + 1, nb_dependent_packages, os.path.basename(repo_path)))
        # print("invoking '%s' in directory '%s'" % (script, repo_path))
        diff_res = None
        diff_cmd = 'git diff --shortstat'
        if _py3:
            subprocess.run(script, shell=True, cwd=repo_path, stdout=subprocess.DEVNULL)
            diff_res = subprocess.run(diff_cmd, shell=True, cwd=repo_path, stdout=subprocess.PIPE)
            diff_output = diff_res.stdout
        else:
            # subprocess.call(cmd, shell=True, stdout=subprocess.DEVNULL)
            proc = subprocess.Popen(cmd, shell=True, cwd=repo_path, stdout=subprocess.PIPE)
            proc.communicate()
            # diff_res = subprocess.call(diff_cmd, shell=True, stdout=subprocess.PIPE)
            diff_proc = subprocess.Popen(diff_cmd, cwd=repo_path, shell=True, stdout=subprocess.PIPE)
            diff_output, diff_err = diff_proc.communicate()
        if diff_output != b'':
            print("adding '%s' to the list of modified_repos" % os.path.basename(repo_path))
            modified_repos.append(repo_path)
            if show_diff:
                print_diff(repo_path)



    # for idx, repo in enumerate(repo_dir_list):
    #     repo_path = os.path.join(directory, repo)
    #     cmd = 'cd %s && %s' % (repo_path, script)
    #     print('repo #%d of %d' % (idx + 1, nb_repos))
    #     print("invoking '%s' in directory '%s'" % (script, repo_path))
    #     diff_res = None
    #     diff_cmd = 'cd %s && git diff --shortstat' % repo_path
    #     if _py3:
    #         subprocess.run(cmd, shell=True, stdout=subprocess.DEVNULL)
    #         diff_res = subprocess.run(diff_cmd, shell=True, stdout=subprocess.PIPE)
    #         diff_output = diff_res.stdout
    #     else:
    #         # subprocess.call(cmd, shell=True, stdout=subprocess.DEVNULL)
    #         proc = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE)
    #         proc.communicate()
    #         # diff_res = subprocess.call(diff_cmd, shell=True, stdout=subprocess.PIPE)
    #         diff_proc = subprocess.Popen(diff_cmd, shell=True, stdout=subprocess.PIPE)
    #         diff_output, diff_err = diff_proc.communicate()
    #     if diff_output != b'':
    #         print("adding '%s' to the list of modified_repos" % repo)
    #         modified_repos.append(repo_path)
    #         if show_diff:
    #             print_diff(repo_path)
    return modified_repos


def clone_repositories(file_path):
    repo_dir = os.path.dirname(file_path)
    src_dir = os.path.join(repo_dir, 'src')
    os.makedirs(src_dir)
    cmd = 'vcs import %s --input %s' % (src_dir, file_path)
    if _py3:
        rc = subprocess.run(
            cmd, shell=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        err_output = rc.stderr
    else:
        proc = subprocess.Popen(
            cmd, shell=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        output, err_output = proc.communicate()
        
    if err_output:
        print('cloning failed', file=sys.stderr)
        print(err_output, file=sys.stderr)
    return src_dir


def save_repos_file(repos_file_content, rosdistro):
    tmpdir = os.path.join('/', 'tmp', 'tmp' + rosdistro + uuid.uuid4().hex[:6].upper())
    print(tmpdir)

    if os.path.isdir(tmpdir):
        os.removedirs(tmpdir)
    os.makedirs(tmpdir)
    repos_file_path = os.path.join(tmpdir, rosdistro + '_all.repos')
    with open(repos_file_path, 'wb') as f:
        f.write(repos_file_content)
    return repos_file_path


def get_repos_list(rosdistro):
    cmd = 'rosinstall_generator moveit --rosdistro %s --deps --upstream-development' % rosdistro
    # cmd = 'rosinstall_generator ros_base --rosdistro %s --deps --upstream-development' % rosdistro
    print('invoking: ' + cmd)
    if _py3:
        result = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        output = result.stdout
        err_output = result.stderr
    else:
        proc = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        output, err_output = proc.communicate()
    if err_output:
        print(err_output)
    return output


if __name__ == '__main__':
    argparser = argparse.ArgumentParser()
    argparser.add_argument(
        '--token',
        help='Github token',)
    argparser.add_argument(
        '--rosdistro',
        type=str,
        required=True,
        choices=['indigo', 'kinetic', 'lunar'],
        help='ROS distribution to update',)
    argparser.add_argument(
        '--branch-name',
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
        help='The provided script will be ran only on these packages and packages that depend on it',)
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
