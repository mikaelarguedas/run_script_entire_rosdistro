#! /usr/bin/env python

from __future__ import print_function

import argparse
import os
import subprocess
import sys
import uuid
import yaml

from github import Github, GithubException, GithubObject
from urllib.parse import urlparse

# TODO Error handling: raise on Fatal, skip on minor errors
# TODO Bail out for non git repos
# TODO support python2
# TODO create fork if needed
# TODO push changes to upstream repo (or fork)
# TODO open PRs
# TODO provide options to skip parts of the process
# TODO add versose mode

def main(token, commit, rosdistro, pr_message, commit_message, branch_name, script):
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
    repos_file_content = list_all_repos(rosdistro)
    file_path = save_repos_file(repos_file_content, rosdistro)
    print(file_path)
    source_dir = clone_repositories(file_path)
    repo_dir_list = run_script_on_repos(source_dir, script, show_diff=False)

    print(repo_dir_list)
    # diff on the entire workspace
    print_diff(source_dir)
    commit_changes(repo_dir_list, commit_message, branch_name)
    if False:
        gh = Github(token)
        # check for fork existence and create one if necessary
        create_fork_if_needed(
            gh, repo_dir_list, repos_file_content, pr_message, commit_message, branch_name)


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


def commit_changes(repo_dir_list, commit_message, branch_name):
    for repo_path in repo_dir_list:
        cmd = 'cd %s && git checkout -b %s && git add . && git commit -m "%s"' % (
            repo_path, branch_name, commit_message)
        print("invoking '%s' in '%s'" % (cmd, repo_path))
        subprocess.run(
            cmd, shell=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)


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
        base_org = gh_repo_dict[repo]['base_org']
        base_repo = gh_repo_dict[repo]['base_repo']
        base_branch = gh_repo_dict[repo]['base_branch']
        if [base_org + '/' + base_repo == repo.full_name for repo in ghuser_repos]:
            print("user '%s' has access to '%s'" % (ghusername, base_org + '/' + base_repo))
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
    subprocess.run('cd %s && vcs diff -s' % directory, shell=True)


def run_script_on_repos(directory, script, show_diff=False):
    repo_dir_list = os.listdir(directory)
    modified_repos = []
    for repo in repo_dir_list:
        repo_path = os.path.join(directory, repo)
        cmd = 'cd %s && %s' % (repo_path, script)
        print("invoking '%s' in directory '%s'" % (script, repo_path))
        subprocess.run(cmd, shell=True, stdout=subprocess.DEVNULL)
        diff_res = None
        diff_res = subprocess.run(
            'cd %s && git diff --shortstat' % repo_path,
            shell=True, stdout=subprocess.PIPE)
        if diff_res.stdout != b'':
            print("adding '%s' to the list of modified_repos" % repo)
            modified_repos.append(repo_path)
            if show_diff:
                print_diff(repo_path)
    return modified_repos


def clone_repositories(file_path):
    repo_dir = os.path.dirname(file_path)
    src_dir = os.path.join(repo_dir, 'src')
    os.makedirs(src_dir)
    cmd = 'vcs import %s --input %s' % (src_dir, file_path)
    rc = subprocess.run(
        cmd, shell=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    if rc.stderr:
        print('cloning failed', file=sys.stderr)
        print(rc.stderr, file=sys.stderr)
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


def list_all_repos(rosdistro):
    # cmd = 'rosinstall_generator ALL --rosdistro %s --deps --upstream-development' % rosdistro
    cmd = 'rosinstall_generator ros_base --rosdistro %s --deps --upstream-development' % rosdistro
    print('invoking: ' + cmd)
    result = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.stderr:
        print(result.stderr)
    return result.stdout


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
    args = argparser.parse_args()

    main(
        token=args.token,
        commit=args.commit,
        rosdistro=args.rosdistro,
        pr_message=args.pr_message,
        commit_message=args.commit_message,
        branch_name=args.branch_name,
        script=args.script,
    )