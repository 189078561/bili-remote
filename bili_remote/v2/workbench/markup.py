"""Small, bounded BBCode subset for user-edited descriptions, never raw HTML."""
import html
import re

TOKENS = re.compile(r'\[(/?)(b|i|u|quote|code|center|img|url)(?:=([^\]\r\n]{1,2048}))?\]', re.I)
TAGS = {'b':'strong','i':'em','u':'u','quote':'blockquote','code':'pre','center':'div'}


def format_description(text, output, safe_url):
    def escape(value):
        if output == 'html':
            return html.escape(value).replace('\n', '<br>')
        if output == 'bbcode':
            return value.replace('[', '&#91;').replace(']', '&#93;')
        return re.sub(r'([\\`*_{}\[\]()#+.!|>~-])', r'\\\1', html.escape(value))

    def render(value, depth=0):
        if depth > 16:
            return escape(value)
        blocks, cursor = [], 0
        for token in TOKENS.finditer(value):
            if token.start() < cursor or token[1]:
                continue
            tag, attribute = token[2].lower(), token[3]
            if attribute is not None and tag != 'url':
                continue
            closing = re.search(r'\[/' + tag + r'\]', value[token.end():], re.I)
            if not closing:
                continue
            end = token.end() + closing.start()
            inner = value[token.end():end]
            if tag in {'url','img'} and not safe_url(attribute if tag=='url' and attribute else inner):
                continue
            blocks.append(escape(value[cursor:token.start()]))
            if tag in {'url','img'}:
                url = attribute if tag=='url' and attribute else inner
                if output == 'html':
                    url=html.escape(url,quote=True)
                    blocks.append(f'<img src="{url}" alt="简介图片">' if tag=='img' else f'<a href="{url}" rel="noopener noreferrer">{escape(inner)}</a>')
                elif output == 'bbcode':
                    blocks.append('[img]'+url+'[/img]' if tag=='img' else '[url='+url+']'+escape(inner)+'[/url]')
                else:
                    blocks.append(('![简介图片]' if tag=='img' else '['+escape(inner)+']')+'('+url.replace('(','%28').replace(')','%29')+')')
            else:
                content = escape(inner) if tag=='code' else render(inner,depth+1)
                if output == 'html':
                    blocks.append(f'<{TAGS[tag]}>{content}</{TAGS[tag]}>')
                elif output == 'bbcode':
                    blocks.append('['+tag+']'+content+'[/'+tag+']')
                else:
                    boundary={'b':'**','i':'*','u':'','center':'','quote':'\n> ','code':'\n```\n'}[tag]
                    blocks.append(boundary+content+(boundary if tag!='quote' else '\n'))
            cursor=end+len(closing[0])
        blocks.append(escape(value[cursor:]))
        return ''.join(blocks)
    return render(text)
