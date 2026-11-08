"""Stateless publication composition. No repository, draft or project dependency."""
import html
import re
from urllib.parse import urlsplit
from ..naming.errors import NamingError
from .markup import format_description


def safe_url(url):
    if not isinstance(url,str) or any(c in url for c in '\r\n\x00[]<>"'):
        return False
    try: parsed=urlsplit(url)
    except ValueError: return False
    return parsed.scheme in {'http','https'} and bool(parsed.netloc) and not parsed.username and not parsed.password

class PublishingService:
    def compose(self, context, release_title, subtitle, overview, artifacts=None, format='bbcode', *, links=None, report_texts=None):
        if format not in {'bbcode','html','markdown'}:
            raise NamingError('invalid_format','发布格式无效')
        errors=[]; warnings=[]
        for key,val in [('release_title',release_title),('subtitle',subtitle),('overview',overview)]:
            if not val.strip(): errors.append({'code':'missing_field','field':key,'message':'缺少 '+key})
        def escape(text):
            if format=='html': return html.escape(text)
            if format=='bbcode': return text.replace('[','&#91;').replace(']','&#93;')
            return re.sub(r'([\\`*_{}\[\]()#+.!|>~-])',r'\\\1',html.escape(text))
        texts=[release_title,subtitle,overview,*list(report_texts or [])]
        if format=='html':
            blocks=[f'<h1>{escape(release_title)}</h1>',f'<p>{escape(subtitle)}</p>','<div>'+format_description(overview,format,safe_url)+'</div>']
            blocks.extend('<pre>'+escape(t)+'</pre>' for t in (report_texts or []))
        else: blocks=[escape(t) for t in [release_title,subtitle] if t] + ([format_description(overview,format,safe_url)] if overview else []) + [escape(t) for t in report_texts or [] if t]
        for artifact in artifacts or []:
            url=artifact.get('url')
            if not url: continue
            if not safe_url(url):
                warnings.append({'code':'invalid_url','message':'已忽略不安全的图片地址'}); continue
            if artifact.get('kind') not in {'screenshot','collage','upload'}: continue
            if format=='html': blocks.append(f'<img src="{html.escape(url,quote=True)}" alt="媒体图片">')
            elif format=='bbcode': blocks.append('[img]'+url+'[/img]')
            else: blocks.append('![媒体图片]('+url.replace('(','%28').replace(')','%29')+')')
        for url in links or []:
            if not safe_url(url):
                warnings.append({'code':'invalid_url','message':'已忽略不安全的链接'}); continue
            if format=='html': blocks.append(f'<p><a href="{html.escape(url,quote=True)}" rel="noreferrer">{html.escape(url)}</a></p>')
            elif format=='bbcode': blocks.append('[url]'+url+'[/url]')
            else: blocks.append('<'+url+'>')
        return {'content':'\n\n'.join(blocks),'validation':{'ready':not errors,'errors':errors,'warnings':warnings},'context_revision':context['revision']}
