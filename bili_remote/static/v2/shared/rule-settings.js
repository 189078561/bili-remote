/* Rule-only editor over the existing optimistic-concurrency memory API. */
(() => {
  const editors=new Map();
  const create=(tag,text,cls)=>{const n=document.createElement(tag);if(text!=null)n.textContent=text;if(cls)n.className=cls;return n;};
  async function mount(host,kind) {
    const state=editors.get(kind)||{loaded:false,dirty:false};editors.set(kind,state);
    host.classList.add('rule-editor');host.textContent='正在读取全局规则…';
    if(!state.loaded){
      state.loading ||= api('/api/v2/naming/memory').then(data=>{state.revision=data.revision.global||0;state.rule=structuredClone(data.effective[kind]||{});state.loaded=true;}).finally(()=>{state.loading=null;});
      try{await state.loading;}
      catch(error){host.textContent=error.message;host.append(button('重新读取',()=>mount(host,kind)));return;}
      if(!host.isConnected)return;
    }
    host.replaceChildren();const isNaming=kind==='naming_rule';
    host.append(create('p','全局默认值用于新选择的资源。系列与季集记忆在工作台“命名详情”中管理；当前资源临时修改不会自动保存到这里。','rule-note'));
    const grid=create('div',null,'rule-grid');host.append(grid);
    function field(key,label,options={}) {
      const wrap=create('label',label),input=create(options.choices?'select':options.textarea?'textarea':'input');
      input.dataset.ruleKey=key;
      if(options.choices)for(const [value,title] of options.choices)input.append(new Option(title,value));
      if(options.textarea){input.rows=3;input.maxLength=500;}
      if(options.checkbox){input.type='checkbox';wrap.className='rule-check';input.checked=state.rule[key]??options.default??false;}
      else {input.value=(options.get?options.get():state.rule[key])??options.default??'';if(!options.choices&&!options.textarea)input.maxLength=100;}
      input.oninput=()=>{
        const value=options.checkbox?input.checked:options.number?Number(input.value):input.value;
        if(options.set)options.set(value);else state.rule[key]=key==='cast_limit'&&value!=='all'?Number(value):value;
        state.dirty=true;update();
      };
      wrap.append(input);(options.wide?host:grid).append(wrap);
    }
    if(isNaming){
      for(const [key,label,def] of [['source','默认片源','WEB-DL'],['group','默认制作组','HDVWEB']])field(key,label,{default:def,get:()=>typeof state.rule[key]==='object'?state.rule[key].value:state.rule[key],set:value=>state.rule[key]={value,source:'manual',manual:true,confirmed:true}});
      const templates={movie:'{Title}.{Year}.{Resolution}.{Source}.{VideoCodec}.{AudioCodec}-{Group}.{Extension}',series:'{Title}.{Year}.S{Season:00}E{Episode:00}.{Resolution}.{Source}.{VideoCodec}.{AudioCodec}-{Group}.{Extension}',anime:'{Title}.{Year}.S{Season:00}E{Episode:00}.{Resolution}.{Source}.{VideoCodec}.{AudioCodec}-{Group}.{Extension}',season:'{Title}.{Year}.S{Season:00}.{Resolution}.{Source}.{VideoCodec}.{AudioCodec}-{Group}'};
      for(const [key,label] of [['movie','电影模板'],['series','剧集模板'],['anime','动画模板'],['season','整季目录模板']])field(`template-${key}`,label,{textarea:true,wide:true,get:()=>state.rule.templates?.[key]||templates[key],set:value=>{state.rule.templates={...state.rule.templates,[key]:value};}});
      host.append(create('p','支持 Title、Year、Season、Episode、Resolution、Source、VideoCodec、AudioCodec、Group、Extension 等既有模板字段；未知媒体参数不会编造。','rule-note'));
    } else {
      field('template_id','副标题模板',{default:'tv_episode',choices:[['tv_episode','电视剧单集'],['tv_season','电视剧整季'],['anime','日本动画'],['movie','电影'],['custom','自定义']]});
      field('cast_limit','演员 / 声优数量',{default:'3',choices:[['3','3 位'],['5','5 位'],['8','8 位'],['all','全部']]});
      field('section_separator','段落分隔符',{default:' | '});field('list_separator','名单分隔符',{default:state.rule.template_id==='anime'?' / ':' '});
      field('show_director','显示导演',{checkbox:true,default:true});
      field('custom_template','自定义副标题模板',{textarea:true,wide:true,set:value=>{state.rule.custom_template=value||null;}});
      host.append(create('p','自定义字段沿用副标题服务：TitleZh、OriginalTitle、Year、Season、Episode、Genres、Cast、VoiceCast、Director。缺失字段会提示；不支持播出平台字段。','rule-note'));
    }
    const actions=create('div',null,'button-row'),status=create('p','', 'rule-note');status.setAttribute('role','status');
    const save=button('保存全局规则',async()=>{
      try {
        const result=await api('/api/v2/naming/memory',{method:'PUT',body:JSON.stringify({scope:'global',expected_revision:state.revision,[kind]:state.rule})});
        state.revision=result.revision;state.dirty=false;
        // Both sections share a revision; invalidate the other clean editor only.
        for(const [key,other] of editors)if(key!==kind&&!other.dirty)other.loaded=false;
        update();status.textContent='全局规则已保存。发布正文不会保存。';
      }catch(e){status.textContent=`未保存：${e.message}。输入已保留；若其他页面修改了规则，请重新读取后调整。`;}
    });
    const reload=button('重新读取',async()=>{if(state.dirty&&!confirm('重新读取会放弃这里尚未保存的规则修改，继续吗？'))return;state.loaded=false;state.dirty=false;await mount(host,kind);});
    function update(){save.dataset.saved=String(!state.dirty);save.disabled=!state.dirty;status.textContent=state.dirty?'规则尚未保存':'';}
    actions.append(save,reload);host.append(actions,status);update();
  }
  function button(label,run){const n=create('button',label,'button secondary');n.type='button';n.onclick=async()=>{n.disabled=true;try{await run();}finally{n.disabled=n.dataset.saved==='true';}};return n;}
  window.V2RuleSettings={mount,isDirty:()=>[...editors.values()].some(x=>x.dirty)};
  addEventListener('beforeunload',event=>{if(window.V2RuleSettings.isDirty()){event.preventDefault();event.returnValue='';}});
})();
