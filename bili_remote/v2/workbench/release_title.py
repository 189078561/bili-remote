"""Independent PT main-title builder, from structured values, never a filename."""
from string import Formatter
from ..naming.errors import NamingError
from ..naming.templates import FIELD_MAP, effective_season, normalize_codec_value, normalize_naming_text, value, season_common_probe

class ReleaseTitleBuilder:
    def build(self, context, rule=None):
        rule=rule or {}
        allowed={'site_profile','template','title_candidate_id','separator','include_hdr','include_channels','include_frame_rate','include_bit_depth','overrides'}
        if not isinstance(rule,dict) or set(rule)-allowed:
            raise NamingError('invalid_release_title_rule','主标题规则含未知字段')
        naming=context.get('effective_rules',{}).get('naming_rule',{})
        files=context.get('files',[])
        episode=context.get('episode') or (files[0].get('episode',{}) if files else {})
        season=episode.get('scope')=='season'
        warnings=[]
        probe=files[0].get('probe',{}) if len(files)==1 else {}
        if len(files)>1:
            probe, warnings=season_common_probe(files)
        candidate=next((t['text'] for t in context.get('titles',[]) if t['id']==rule.get('title_candidate_id')),None)
        if rule.get('title_candidate_id') and candidate is None:
            raise NamingError('invalid_title','主标题名称候选不存在')
        values={'Title':candidate or value(context.get('selected_title')),
            'Year':value(context.get('metadata',{}).get('year')),
            'Season':effective_season(episode, naming), 'Episode':None if season else value(episode.get('episode')),
            'Source':value(naming.get('source')), 'Group':value(naming.get('group'))}
        values.update({field:value(probe.get(key)) for field,key in FIELD_MAP.items()})
        overrides=rule.get('overrides') or {}
        if not isinstance(overrides,dict) or set(overrides)-set(values):
            raise NamingError('invalid_release_title_rule','主标题覆盖字段无效')
        for key,item in overrides.items():
            if isinstance(item,dict):
                if item.get('confirmed') is not True or item.get('manual') is not True:
                    raise NamingError('invalid_release_title_rule','覆盖必须为明确确认的人工字段')
                values[key]=value(item)
            elif isinstance(item,(str,int)) and not isinstance(item,bool): values[key]=item
            else: raise NamingError('invalid_release_title_rule','覆盖值无效')
        if naming.get('episode_numbering') == 'continuous':
            values['Season'] = None
        for key, minimum in (('Season', 0), ('Episode', 1)):
            number = values[key]
            if number is not None and (type(number) is not int or number < minimum):
                raise NamingError('invalid_episode', '季号须为非负整数，集号须为正整数')
        values['VideoCodec']=normalize_codec_value('VideoCodec',values.get('VideoCodec'))
        values['AudioCodec']=normalize_codec_value('AudioCodec',values.get('AudioCodec'))
        series=(context.get('identity') or {}).get('media_type') in {'series','anime'} or naming.get('template_id') in {'series','anime','season'}
        default='{Title} {Year}'+(' S{Season:00}'+('' if season else 'E{Episode:00}') if series else '')+' {Resolution} {Source} {VideoCodec} {AudioCodec}-{Group}'
        template=rule.get('template',default)
        if not isinstance(template,str) or not 1<=len(template)<=500:
            raise NamingError('invalid_template','主标题模板须为 1–500 字符')
        try: parts=list(Formatter().parse(template))
        except ValueError as exc: raise NamingError('invalid_template','主标题模板格式错误') from exc
        rendered=[]
        for literal,field,spec,conversion in parts:
            rendered.append(literal)
            if field is None: continue
            if field not in values or conversion or spec not in {'','00'} or (spec and field not in {'Season','Episode'}):
                raise NamingError('invalid_template','主标题模板变量无效')
            item=values[field]
            if item in (None,'',[]):
                warnings.append({'code':'missing_field','field':field,'message':'主标题缺少 '+field})
                if field in {'Season','Episode'} and rendered[-1].endswith('S' if field=='Season' else 'E'):
                    rendered[-1]=rendered[-1][:-1]
                continue
            if spec:
                if type(item) is not int: raise NamingError('invalid_episode','季集号必须为整数')
                item=str(item).zfill(2)
            if isinstance(item,list): item=' '.join(map(str,item))
            if isinstance(item,dict):
                try: item=f"{item['numerator']/item['denominator']:.3f}".rstrip('0').rstrip('.')
                except (KeyError,TypeError,ZeroDivisionError): item=''
            rendered.append(str(item))
        for switch, field in [('include_hdr','HDR'),('include_channels','AudioChannels'),('include_frame_rate','FrameRate'),('include_bit_depth','BitDepth')]:
            if switch in rule and type(rule[switch]) is not bool:
                raise NamingError('invalid_release_title_rule','主标题开关必须为布尔值')
            if rule.get(switch) and field not in {part[1] for part in parts}:
                item=values.get(field)
                if item in (None,'',[]):
                    warnings.append({'code':'missing_field','field':field,'message':'主标题缺少 '+field})
                else:
                    if isinstance(item,list): item=' '.join(map(str,item))
                    if isinstance(item,dict):
                        try: item=f"{item['numerator']/item['denominator']:.3f}".rstrip('0').rstrip('.')
                        except (KeyError,TypeError,ZeroDivisionError): item=''
                    rendered.extend([' ',str(item)])
        separator=rule.get('separator','.')
        if separator not in {' ','.','_','-'}:
            raise NamingError('invalid_release_title_rule','主标题分隔符无效')
        text=normalize_naming_text(''.join(rendered), separator)
        return {'text':text,'warnings':warnings,'context_revision':context['revision']}
