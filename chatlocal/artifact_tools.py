"""File tools reuse the existing Harness scope, evidence budget and citations."""
from .artifacts import Artifacts, ArtifactError


def schemas(tool,filters,text,page,offset):
    fields={k:filters[k] for k in ('platform','conversation_id','start','end')}
    fields.update(query=text,sender=text,extension=text,limit=page,offset=offset,
                  file_id=dict(type='integer',minimum=1))
    return [
        tool('search_files','搜索文件名/群/上传人/日期/扩展名元数据，query可空；不下载正文。文件消息不等于完整QQ群目录。'
             'metadata证据F编号只能说明名称、来源等，不能据此推测正文。',fields),
        tool('prepare_file','仅当用户问题需要正文时，按需取得并本地解析一个候选文件；每轮最多3个，每个32MiB。'
             '仅缓存与解析，不把全文传给模型。解析成功后search_file_content/read_file_chunks。'
             '文件不可用、超限或扫描页无文字需明确告知，不可编造。',{'file_id':dict(type='integer',minimum=1)},['file_id']),
        tool('search_file_content','在已解析文件正文中搜索；可指定file_id（元数据id）。返回有原文和真实位置的有限chunk，'
             '用其citation_id填写artifact_evidence_ids。未解析文件不会命中。',fields),
        tool('read_file_chunks','分页读取指定文件的原文chunk与位置，不会发送整个文件。可接续search_file_content命中的ordinal。',
             {'file_id':dict(type='integer',minimum=1),'offset':offset,
              'limit':dict(type='integer',minimum=1,maximum=8,default=4)},['file_id']),
    ]


class ArtifactTools:
    def _files(self):
        if not hasattr(self,'file_evidence'):
            self.file_evidence={};self.files_prepared=set()
        return Artifacts(self.store)

    def _admit_files(self,result):
        self._files()
        accepted=[]
        for item in result['files']:
            key=item['citation_id']
            if key in self.file_evidence:accepted.append(self.file_evidence[key]);continue
            import json
            size=len(json.dumps(item,ensure_ascii=False))
            if self.used_chars+size>self.max_chars or len(self.file_evidence)>=100:
                self.incomplete=True;break
            self.used_chars+=size;self.file_evidence[key]=item;accepted.append(item)
        return dict(result,files=accepted,budget_limited=len(accepted)<len(result['files']),remaining_chars=self.max_chars-self.used_chars)

    def _search_files(self,args):
        return self._admit_files(self._files().search(dict(args,source_id=args.get('file_id')),self.plan))

    def _search_file_content(self,args):
        return self._admit_files(self._files().search(dict(args,source_id=args.get('file_id')),self.plan,body=True))

    def _prepare_file(self,args):
        layer=self._files();sid=args['file_id']
        layer.get(sid,self.plan)  # Hard scope before any local read/download.
        if len(self.files_prepared)>=3 and sid not in self.files_prepared:
            return dict(error='本轮最多按需解析3个文件；请优先使用已有索引。')
        self.files_prepared.add(sid)
        try:return dict(file=layer.prepare(sid),note='准备完成仅代表本地解析；正文请继续search_file_content或read_file_chunks。')
        except ArtifactError as exc:return dict(error=str(exc))
        except OSError:return dict(error='本地文件当前不可读；未将正文交给模型，请检查客户端附件后重试。')

    def _read_file_chunks(self,args):
        return self._admit_files(self._files().chunks(args['file_id'],args.get('offset',0),args.get('limit',4),self.plan))
