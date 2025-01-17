import os
import re
import yaml
import networkx as nx
import subprocess
from urllib.request import urlopen
from mathlibtools.lib import PortStatus, FileStatus
from sys import argv
from pathlib import Path
import jinja2
import shlex
import git

import_re = re.compile(r"^import ([^ ]*)")
synchronized_re = re.compile(r".*SYNCHRONIZED WITH MATHLIB4.*")
hash_re = re.compile(r"[0-9a-f]*")

# Not using re.compile as this is passed to git which uses a different regex dialect:
# https://www.sjoerdlangkemper.nl/2021/08/13/how-does-git-diff-ignore-matching-lines-work/
comment_git_re = r'\`(' + r'|'.join([
    re.escape("> THIS FILE IS SYNCHRONIZED WITH MATHLIB4."),
    re.escape("> https://github.com/leanprover-community/mathlib4/pull/") + r"[0-9]*",
    re.escape("> Any changes to this file require a corresponding PR to mathlib4."),
    r"",
]) + r")" + "\n"

def make_old(env: jinja2.Environment, html_root: Path, mathlib_dir: Path):
    mathlib_repo = git.Repo(mathlib_dir)

    def mk_label(path: Path) -> str:
        rel = path.relative_to((mathlib_dir / 'src'))
        return str(rel.with_suffix('')).replace(os.sep, '.')

    graph = nx.DiGraph()

    for path in (mathlib_dir / 'src').glob('**/*.lean'):
        if path.relative_to(mathlib_dir / 'src').parts[0] in ['tactic', 'meta']:
            continue
        graph.add_node(mk_label(path))

    synchronized = dict()

    for path in (mathlib_dir / 'src').glob('**/*.lean'):
        if path.relative_to(mathlib_dir / 'src').parts[0] in ['tactic', 'meta']:
            continue
        label = mk_label(path)
        for line in path.read_text().split('\n'):
            m = import_re.match(line)
            if m:
                imported = m.group(1)
                if imported.startswith('tactic.') or imported.startswith('meta.'):
                    continue
                if imported not in graph.nodes:
                    if imported + '.default' in graph.nodes:
                        imported = imported + '.default'
                    else:
                        imported = 'lean_core.' + imported
                graph.add_edge(imported, label)
            if synchronized_re.match(line):
                synchronized[label] = True


    data = PortStatus.deserialize_old().file_statuses
    # First make sure all nodes exists in the data set
    for node in graph.nodes:
        data.setdefault(node, FileStatus())

    allDone = dict()
    parentsDone = dict()
    verified = dict()
    touched = dict()
    warnings = []
    for node in graph.nodes:
        if data[node].mathlib3_hash:
            verified[node] = data[node].mathlib3_hash
            fname = "src" + os.sep + node.replace('.', os.sep) + ".lean"
            git_command = ['git',
                # disable abbreviations so that the full sha is in the output
                '-c', 'core.abbrev=no',
                'diff', '--exit-code',
                f'--ignore-matching-lines={comment_git_re}',
                data[node].mathlib3_hash + "..HEAD", "--", fname]
            result = subprocess.run(git_command, cwd=mathlib_dir, capture_output=True, encoding='utf8')
            if result.returncode == 1:
                commits = [
                    c for c in mathlib_repo.iter_commits(f'{data[node].mathlib3_hash}..HEAD', fname)
                    if not c.summary.startswith('chore(*): add mathlib4 synchronization comments')
                        and c.hexsha != '448144f7ae193a8990cb7473c9e9a01990f64ac7'
                ]
                touched[node] = (result.stdout, commits)
        elif data[node].ported:
            warnings.append("Bad status for " + node + "\n." +
                "Expected 'Yes MATHLIB4-PR MATHLIB-HASH'")
        ancestors = nx.ancestors(graph, node)
        if all(data[imported].ported for imported in ancestors) and not data[node].ported:
            allDone[node] = (len(nx.descendants(graph, node)), data[node].comments or "")
        else:
            if all(data[imported].ported for imported in graph.predecessors(node)) and not data[node].ported:
                parentsDone[node] = (len(nx.descendants(graph, node)), data[node].comments or "")

    allDone = dict(sorted(allDone.items(), key=lambda item: -item[1][0]))

    parentsDone = dict(sorted(parentsDone.items(), key=lambda item: -item[1][0]))
    needsSync = []
    for node in graph.nodes:
        if data[node].ported and not node in synchronized:
            needsSync.append((node, data[node]))
    unverified = [
        (node, data[node])
        for node in graph.nodes
        if data[node].ported and node not in verified
    ]

    with (html_root / 'old.html').open('w') as index_f:
        index_f.write(env.get_template('old.j2').render(
            warnings=warnings,
            allDone=allDone, parentsDone=parentsDone, needsSync=needsSync, unverified=unverified
        ))
    with (html_root / 'out-of-sync.html').open('w') as index_f:
        index_f.write(env.get_template('out-of-sync.j2').render(
            touched=touched, verified=verified,
            head_sha=mathlib_repo.head.object.hexsha
        ))
