"""Bounded local parser worker. No network, macros, rendering or remote models."""
import json
import sys
import zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from chatlocal.config import ROOT
from chatlocal.artifacts import TEXT_TYPES,MAX_CONFIRMED_BYTES

MAX_CHUNKS=1200
MAX_CHARS=1200000
MAX_PAGES=300


def compact_table(markdown):
    # Markdown column alignment can add tens of thousands of padding characters
    # to a one-cell DOCX. Keep cell text and separators without alignment padding.
    lines=[]
    for line in markdown.splitlines():
        if line.startswith('|') and line.endswith('|'):
            cells=[c.strip() for c in line[1:-1].split('|')]
            cells=['---' if c and set(c)<=set('-: ') else c for c in cells]
            line='| '+' | '.join(cells)+' |'
        lines.append(line)
    return '\n'.join(lines)


def parse(path,extension):
    if path.stat().st_size>MAX_CONFIRMED_BYTES:raise ValueError('解析文件超过100 MiB。')
    chunks=[];total=0;partial=False;note=[]
    def add(text,locator):
        nonlocal total,partial
        text=str(text).strip()
        for start in range(0,len(text),1600):
            part=text[start:start+1600]
            if len(chunks)>=MAX_CHUNKS or total+len(part)>MAX_CHARS:
                partial=True;return
            chunks.append(dict(text=part,locator=dict(locator,part=start//1600+1,method='native_text')));total+=len(part)
    if extension in ('docx','pptx','xlsx'):
        with zipfile.ZipFile(path) as archive:
            entries=archive.infolist()
            if len(entries)>10000 or sum(v.file_size for v in entries)>200*1024*1024:
                raise ValueError('Office压缩结构超过展开预算，未解析。')
            if any(v.file_size>64*1024*1024 or v.file_size>max(1,v.compress_size)*1000 for v in entries):
                raise ValueError('Office内部文件异常膨胀，未解析。')
    parser=''
    if extension=='pdf':
        from pypdf import PdfReader
        doc=PdfReader(path)
        if doc.is_encrypted and not doc.decrypt(''):raise ValueError('PDF有密码，V1不尝试解密。')
        partial=len(doc.pages)>MAX_PAGES
        empty=0
        for i,page in enumerate(doc.pages[:MAX_PAGES],1):
            text=page.extract_text() or ''
            if not text.strip():empty+=1
            add(text,dict(page=i))
        parser='pypdf 6.19.0'
        note.append('原生文本提取；未运行OCR，复杂表格/多栏的阅读顺序需核对原文件。')
        if empty:note.append(f'{empty}页没有原生文字，可能是扫描/图片页。');partial=True
    elif extension in ('docx','pptx','xlsx'):
        # Direct Docling backends avoid initializing PDF layout/VLM pipelines.
        from io import BytesIO
        from docling.datamodel.document import InputDocument
        from docling.datamodel.base_models import InputFormat
        if extension=='docx':
            from docling.backend.msword_backend import MsWordDocumentBackend as Backend
        elif extension=='pptx':
            from docling.backend.mspowerpoint_backend import MsPowerpointDocumentBackend as Backend
        else:
            from docling.backend.msexcel_backend import MsExcelDocumentBackend as Backend
        stream=BytesIO(path.read_bytes())
        inp=InputDocument(path_or_stream=stream,format=getattr(InputFormat,extension.upper()),backend=Backend,
            filename='document.'+extension)
        backend=inp._backend
        try:
            doc=backend.convert()
            section=[]
            for item,depth in doc.iterate_items():
                label=str(getattr(item,'label',''))
                text=getattr(item,'text','')
                if 'section_header' in label or label=='title':
                    section=section[:max(0,depth-1)]+[text]
                locator=dict(kind=label,section=' / '.join(section),docling_ref=item.self_ref)
                prov=getattr(item,'prov',[])
                if prov:
                    # Docling PPT page numbers represent slides. DOCX has no
                    # stable rendered page number: never fabricate one.
                    if extension=='pptx':locator['slide']=prov[0].page_no
                    elif extension=='xlsx':locator['sheet_index']=prov[0].page_no
                if hasattr(item,'export_to_markdown') and 'table' in label:
                    text=compact_table(item.export_to_markdown(doc=doc))
                if text:add(text,locator)
                if partial:break
            parser='Docling 2.129.0 '+extension.upper()
            note.append('原生Office结构；未分析嵌入图片、图形或执行公式/宏。DOCX仅提供章节和块位置，不推测页码。')
        finally:backend.unload()
    elif extension in TEXT_TYPES:
        data=path.read_bytes()
        text=None
        for enc in ('utf-8-sig','utf-16' if data[:2] in (b'\xff\xfe',b'\xfe\xff') else 'gb18030'):
            try:text=data.decode(enc);break
            except UnicodeError:pass
        if text is None:raise ValueError('文本编码无法可靠识别。')
        lines=text.splitlines();section='';buffer=[];first=1
        for n,line in enumerate(lines,1):
            if extension=='md' and line.startswith('#'):section=line.strip('# ').strip()
            if sum(map(len,buffer))+len(line)>1400 and buffer:
                add('\n'.join(buffer),dict(line_start=first,line_end=n-1,section=section));buffer=[];first=n
            buffer.append(line)
            if partial:break
        if buffer:add('\n'.join(buffer),dict(line_start=first,line_end=len(lines),section=section))
        parser='plain-text-v1';note.append('原生文本；代码仅作为文字读取，不执行。CSV保留行文本。')
    else:raise ValueError('V1暂不解析此类型。')
    if partial:note.append('解析不完整：存在无文字页或达到页数/字符/chunk上限。')
    return dict(chunks=chunks,status='PARTIAL' if partial and chunks else 'PARSED' if chunks else 'NO_TEXT',
        parser=parser,note=' '.join(note))


if __name__=='__main__':
    output=Path(sys.argv[3]).resolve()
    if not output.is_relative_to(ROOT):raise ValueError('输出须在工作目录内')
    try:result=parse(Path(sys.argv[1]),sys.argv[2])
    except Exception as exc:
        result=dict(error=str(exc) if isinstance(exc,ValueError) else '解析器失败：'+type(exc).__name__)
    output.write_text(json.dumps(result,ensure_ascii=False),'utf-8')
