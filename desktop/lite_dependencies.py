"""Copy only installed distributions required by the MCP edition, using RECORD."""
from importlib import metadata
from pathlib import Path
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


def distributions(root):
    queue=[Requirement(line.strip()) for line in (root/'requirements-mcp.txt').read_text().splitlines() if line.strip() and not line.startswith('#')]
    selected={};seen=set()
    while queue:
        req=queue.pop();key=(canonicalize_name(req.name),tuple(sorted(req.extras)))
        if key in seen:continue
        seen.add(key);dist=metadata.distribution(req.name)
        if req.specifier and dist.version not in req.specifier:raise RuntimeError(f'Installed {req.name} {dist.version} does not match {req.specifier}')
        selected[key[0]]=dist
        for raw in dist.requires or []:
            child=Requirement(raw)
            if not child.marker or any(child.marker.evaluate({'extra':extra}) for extra in ('',*req.extras)):
                queue.append(child)
    if any('harness' in n or n.startswith('gradio') for n in selected):raise RuntimeError('MCP dependency closure contains an Agent runtime')
    return selected


def copy_dependencies(root,copy):
    selected=distributions(root)
    site=(root/'.venv/Lib/site-packages').resolve()
    for dist in selected.values():
        for entry in dist.files or []:
            source=Path(dist.locate_file(entry)).resolve()
            if not source.is_relative_to(site):continue
            rel=source.relative_to(site)
            if '__pycache__' in rel.parts or source.suffix=='.pyc' or source.name=='direct_url.json':continue
            # Only jieba.cut/lcut are used: keep its dictionary and finalseg,
            # omit optional POS/tagging/keyword models and bundled tests.
            if rel.parts[0]=='jieba' and len(rel.parts)>1 and rel.parts[1] in ('analyse','posseg','lac_small'):continue
            if any(part in ('tests','test','Demos') for part in rel.parts) or source.suffix=='.chm':continue
            if source.is_file():copy(source,Path('runtime/Lib/site-packages')/rel)
    return sorted([{'name':d.metadata['Name'],'version':d.version} for d in selected.values()],key=lambda d:d['name'].lower())
