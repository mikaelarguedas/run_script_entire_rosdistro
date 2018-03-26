#! /usr/bin/env python

from __future__ import print_function

import argparse
import copy
import json
import os
import subprocess
import sys
import uuid
import yaml

from github import Github, GithubException, UnknownObjectException  # , GithubObject

_py3 = sys.version_info[0] >= 3
if _py3:
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
    print('cloning repositories')
    source_dir = clone_repositories(file_path)
    print('running script on packages')
    modified_pkgs_dict, modified_repos_list = run_script_on_repos(
        source_dir, script, package_list, show_diff=False)

    # diff on the entire workspace
    # print_diff(source_dir)
    print('commiting changes')
    commit_changes(modified_pkgs_dict, commit_message, branch_name)
    # if False:
    if True:
        gh = Github(token)
        # check for fork existence and create one if necessary
        print('check for repo access or forks')
        forks_to_create, repos_to_push = check_if_fork_needed(
            gh, modified_repos_list, repos_file_content, pr_message, commit_message, branch_name)
        print(forks_to_create)
        print(repos_to_push)
        create_forks(gh, forks_to_create)

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
        # print("'%s'" % output)
        cmd = 'git add . && git commit -m "[%s] %s"' % (
            package_name, commit_message)
        if output != branch_name:
            cmd = 'git checkout -b %s && ' % branch_name + cmd
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


def check_if_fork_needed(
        gh, repo_dir_list, repos_file_content, pr_message, commit_message, branch_name):
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
    print()
    print(repo_dir_list)
    print()
    forks_to_create = []
    repositories_to_push_without_forking = []
    skipped_repos = []
    # build list of modified repos
    for repo_path in repo_dir_list:
        head_repo = ''
        repo = os.path.basename(repo_path)
        # print(gh_repo_dict[repo])
        base_org = gh_repo_dict[repo]['base_org']
        base_repo = gh_repo_dict[repo]['base_repo']
        base_branch = gh_repo_dict[repo]['base_branch']
        repo_full_name = base_org + '/' + base_repo
        ghuser_repos_full_names = [user_repo.full_name for user_repo in ghuser_repos]
        if repo_full_name in ghuser_repos_full_names:
            print("user '%s' has access to '%s'" % (ghusername, repo_full_name))
            head_repo = gh.get_repo(repo_full_name)
            repositories_to_push_without_forking.append(repo_full_name)
        else:
            # print("user '%s' does not have access to '%s'\n"
            #       "Maybe he has access to a fork?\n" % (ghusername, base_org + '/' + base_repo))
            base_repo_object = gh.get_repo(repo_full_name)
            try:
                base_repo_object.full_name
            except UnknownObjectException as e:
                print("'%s' is not a github repository, skipping...\n" % repo_full_name)
                skipped_repos.append(repo_full_name)
                continue
            fork_list = base_repo_object.get_forks()
            # try:
            #     fork_list = list_forks(base_org, base_repo)
            #     # if fork_list:
            #     #     print(len(fork_list))
            # except GithubException as exc:
            #     print('Exception happened: %s' % exc)
            #     pass  # 404 or unauthorized, but unauthorized should have been caught above
            for fork in fork_list:
                if fork.full_name in ghuser_repos_full_names:
                    head_repo = fork
                    print("User has access to a fork! '%s'" % fork.full_name)
                    repositories_to_push_without_forking.append(fork.full_name)
                    break
            else:
                print("NO FORK FOUND, NEED TO CREATE A FORK OF '%s'\n" % repo_full_name)
                forks_to_create.append(repo_full_name)
        # print(head_repo)
    print('repositories to push to: %s\n' % repositories_to_push_without_forking)
    print('forks to create: %s\n' % forks_to_create)
    print('skipped repositories: %s\n' % skipped_repos)
    return forks_to_create, repositories_to_push_without_forking


def create_forks(gh, forks_to_create):
    for fork in forks_to_create:
        cmd = "gh.create_fork('%s')" % fork
        print("creating fork calling: '%s'" % cmd)

    # returns the list of forked Github.Repository
    # TODO complete this
    return []


def add_new_remotes(gh, forks_to_create):
    for fork in forks_to_create:
        cmd = "git remote add %s %s" % (remote_name, remote_url)
        print("creating fork calling: '%s' in '%s'" % cmd)


# def push_changes(gh, forks_to_create):
#     for fork in forks_to_create:
#         cmd = "gh.create_fork('%s')" % fork
#         print("creating fork calling: '%s'" % cmd)


# def open_pull_requests(gh, forks_to_create):
#     for fork in forks_to_create:
#         cmd = "gh.create_fork('%s')" % fork
#         print("creating fork calling: '%s'" % cmd)


def print_diff(directory):
    cmd = 'vcs diff -s'
    if _py3:
        subprocess.run(cmd, cwd=directory, shell=True)
    else:
        subprocess.call(cmd, cwd=directory, shell=True)


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
        if _py3:
            subprocess.run(cmd, shell=True, stdout=subprocess.PIPE)
            depends_res = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE)
            tmpdepends_list = depends_res.stdout
        else:
            proc = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE)
            tmpdepends_list, stderr_output = proc.communicate()
        # print('tmpdepends_list: %s' % tmpdepends_list)
        # print(tmpdepends_list.split('\n'))
        # print(len(tmpdepends_list.split('\n')))
        for pkg in tmpdepends_list.split('\n'):
            if pkg == '':
                continue
            # print(pkg)
            dependent_packages.append(pkg)
    nb_dependent_packages = len(dependent_packages)
    # print(dependent_packages)
    print(nb_dependent_packages)
    package_locations = {}
    # now find the location of the selected packages
    #  rospack find <pkg>
    for pkg in dependent_packages:
        cmd = 'rospack find %s' % pkg
        if _py3:
            depends_res = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE)
            package_location = depends_res.stdout
        else:
            proc = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE)
            package_location, _ = proc.communicate()
        if not os.path.isdir(package_location.rstrip('\n')):
            print("package_location '%s' is not a directory" % package_location.rstrip('\n'))
        else:
            package_locations[pkg] = package_location.rstrip('\n')

    modified_pkgs = {}
    modified_repos = []
    for idx, pkg in enumerate(package_locations.keys()):
        pkg_path = package_locations[pkg]
        cmd = script
        print("package #%d of %d: '%s'" % (idx + 1, nb_dependent_packages, pkg))
        diff_res = None
        diff_cmd = 'git diff --shortstat'
        if _py3:
            subprocess.run(script, shell=True, cwd=pkg_path, stdout=subprocess.DEVNULL)
            diff_res = subprocess.run(diff_cmd, shell=True, cwd=pkg_path, stdout=subprocess.PIPE)
            diff_output = diff_res.stdout
        else:
            proc = subprocess.Popen(cmd, shell=True, cwd=pkg_path, stdout=subprocess.PIPE)
            proc.communicate()
            diff_proc = subprocess.Popen(diff_cmd, cwd=pkg_path, shell=True, stdout=subprocess.PIPE)
            diff_output, diff_err = diff_proc.communicate()
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
    # cmd = 'rosinstall_generator ALL --rosdistro %s --deps --upstream-development' % rosdistro
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


def json_loads(resp):
    """Handle parsing json from an HTTP response for both Python 2 and Python 3."""
    try:
        charset = resp.headers.getparam('charset')
        charset = 'utf8' if not charset else charset
    except AttributeError:
        charset = resp.headers.get_content_charset()

    return json.loads(resp.read().decode(charset))


def list_forks(org, repo):
    current_page = 1
    fork_list = []
    while True:
        url = 'https://api.github.com/repos/%s/%s/forks?per_page=100&page=%s' % \
            (org, repo, current_page)
        try:
            response = urlopen(url, timeout=6)
        except URLError as ex:
            print(ex, file=sys.stderr)
            return fork_list

        # url = None
        if response.getcode() in [200, 202]:
            content = response.read().decode('utf-8')
            forks = json.loads(content)
            current_page += 1
            if not forks:
                return fork_list
            for fork in forks:
                fork_list.append(fork['full_name'])


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
