(function () {
 "use strict";
 const labels = {
   filename: "文件名", size: "大小", resolution: "分辨率", aspect: "画面比例",
   fps: "FPS", video: "视频编码", audio: "音频编码", duration: "时长"
 };
 const sample = {
   filename: "山湖晨光.2160p.WEB-DL.H264.AAC.mp4", size: "5.55 GB",
   resolution: "3840 × 1608", aspect: "2.39∶1", fps: "24.00",
   video: "H264", audio: "AAC", duration: "02:40:25"
 };
 const trims = {movie: [90, 120], series: [90, 180], anime: [90, 90]};
 const duration=9625;
 const fmt=t=>[Math.floor(t/3600),Math.floor(t/60)%60,t%60].map(n=>String(n).padStart(2,'0')).join(':');
 const fonts={sans:'system-ui, sans-serif',serif:'"Noto Serif CJK SC", SimSun, serif',mono:'"Courier New", monospace'};
 // Split into contiguous rows. Each row must receive at least one field.
 // Minimise uneven widths while keeping the user's field order.
 function textRows(words, requested, ctx, width) {
  const pieces = [];
  for (const word of words) {
   let part = '';
   for (const char of word) {
    if (part && ctx.measureText(part + char).width > width) {
     pieces.push(part); part = '';
    }
    part += char;
   }
   if (part) pieces.push(part);
  }
  const n = pieces.length;
  if (!n) return [];
  const line = (i,j) => pieces.slice(i,j).join('    ');
  const widths = pieces.map((_,i) => Array.from({length:n-i},(_,k) => ctx.measureText(line(i,i+k+1)).width));
  for (let rows = Math.min(requested,n); rows <= n; rows++) {
   const ideal = ctx.measureText(line(0,n)).width / rows;
   const cost = Array.from({length:rows+1},() => Array(n+1).fill(Infinity));
   const previous = cost.map(row => row.map(() => -1)); cost[0][0]=0;
   for(let r=1;r<=rows;r++) {
    for(let end=r;end<=n-(rows-r);end++) {
     for(let start=r-1;start<end;start++) {
      const w=widths[start][end-start-1];
      const score=cost[r-1][start]+(w-ideal)**2;
      if(w<=width && score<cost[r][end]){cost[r][end]=score;previous[r][end]=start}
     }
    }
   }
   if(previous[rows][n]===-1)continue;
   const result=[];let end=n;
   for(let r=rows;r>0;r--){const start=previous[r][end];result.unshift(line(start,end));end=start}
   return result;
  }
  return pieces;
 }

 // Calm landscape illustration in native source coordinates so crop and fit are visible.
 const scenery = document.createElement('canvas');
 scenery.width=960;scenery.height=402;
 const art=scenery.getContext('2d');
 const sky=art.createLinearGradient(0,0,0,402);
 sky.addColorStop(0,'#b1cbd0');sky.addColorStop(1,'#f8e5c3');
 art.fillStyle=sky;art.fillRect(0,0,960,402);
 art.fillStyle='#f8edcb';art.beginPath();art.arc(685,113,38,0,Math.PI*2);art.fill();
 function ridge(fill,coords){
  art.beginPath();art.moveTo(0,402);coords.forEach(([x,y])=>art.lineTo(x,y));
  art.lineTo(960,402);art.closePath();art.fillStyle=fill;art.fill();
 }
 ridge('#a5b9ae',[[0,208],[82,169],[181,200],[320,122],[409,170],[461,138],[598,204],[707,159],[870,207],[960,149]]);
 ridge('#809d93',[[0,221],[98,201],[202,221],[300,190],[444,236],[533,208],[643,224],[787,187],[868,153],[960,194]]);
 art.fillStyle='#adc4bf';art.fillRect(0,252,960,150);
 ridge('#54786e',[[0,237],[84,224],[166,238],[246,244],[336,269],[232,276],[154,293],[0,314],[0,402],[960,402]]);
 ridge('#3e655b',[[0,392],[136,351],[292,340],[405,321],[554,326],[675,303],[745,273],[876,247],[960,224]]);
 function sampleTimes(options) {
   if (options.sampling === "timestamps") {
     const values = options.times.map(value => String(value).trim()).filter(Boolean);
     if (!values.length || values.length > 120) throw Error("请填写 1–120 个时间点。");
     const times = values.map(value => {
       if (!/^(?:\d{1,3}:)?[0-5]?\d:[0-5]?\d(?:\.\d{1,3})?$/.test(value)) {
         throw Error("请按 MM:SS 或 HH:MM:SS.mmm 填写时间点。");
       }
       return value.split(":").reduce((seconds, part) => seconds * 60 + Number(part), 0);
     });
     if (new Set(times).size !== times.length) throw Error("时间点有重复，请检查。");
     if (times.some(time => time > duration - .05)) throw Error("时间点超出示意素材时长 02:40:25。");
     return times.sort((left, right) => left - right);
   }
   const count = options.single || options.layout === "count" ? options.count : options.rows * options.cols;
   if (!Number.isInteger(count) || count < 1 || count > 120) throw Error("请填写有效的截图数量或拼图行列。");
   const span = duration - options.end - options.start;
   if (span < 1) throw Error("示意素材跳过片头片尾后，取帧范围不足一秒。");
   return Array.from({length: count}, (_, index) => {
     // Stable sample times keep the preview steady while editing other fields.
     const fraction = options.sampling === "uniform" ? .5 : (((index + 1) * 2654435761) >>> 0) / 4294967296;
     return options.start + (index + fraction) * span / count;
   });
 }

 function compose(canvas, options, frame, detail, times) {
   const {single, fit, ratio, fields, info, font, family, lines: requestedRows, pct} = options;
   const count = times.length;
   const columns = single ? 1 : options.cols;
   const rows = single ? 1 : options.layout === "count" ? Math.ceil(count / columns) : options.rows;
   if (columns < 1 || columns > 10 || rows < 1 || rows > 12) throw Error("拼图支持 1–10 列、1–12 行。");
   if (count > rows * columns && !single) throw Error("固定网格容纳不了这些时间点，请增加行列或使用按总数排布。");
   const width = single ? options.singleWidth || 3840 : options.tileWidth;
   const maxWidth = single ? 3840 : 1920;
   if (width < 240 || width > maxWidth) throw Error(`画面宽度应为 240–${maxWidth} px。`);
   const sourceRatio = 3840 / 1608;
   const displayRatio = single || ratio === "original" ? sourceRatio : {wide: 16 / 9, classic: 4 / 3, square: 1}[ratio];
   const scaledHeight = Math.round(width / displayRatio);
   const height = single ? scaledHeight + scaledHeight % 2 : scaledHeight;
   const styled = !single || options.style === "script";
   const gap = single ? (styled ? 3 : 0) : options.gap;
   const timestampBar = styled ? 30 : 0;
   const margin = styled ? 12 : 0;
   const cellWidth = width + 2 * gap + margin;
   const cellHeight = height + timestampBar + 2 * gap + margin;
   const canvasWidth = columns * cellWidth;
   const gridHeight = rows * cellHeight;

   const ctx = canvas.getContext("2d");
   const fontStyle = `${font}px ${fonts[family]}`;
   ctx.font = fontStyle;
   const words = !single && info ? fields.map(key => `${labels[key]}：${sample[key]}`) : [];
   const lines = textRows(words, requestedRows, ctx, canvasWidth - 16);
   const targetHeight = Math.round(gridHeight * pct / (100 - pct));
   const lineHeight = Math.ceil(font * 1.25);
   const headerHeight = lines.length ? Math.max(targetHeight, lines.length * lineHeight + 10) : 0;
   const canvasHeight = gridHeight + headerHeight;
   if (canvasWidth > 20000 || canvasHeight > 40000 || canvasWidth * canvasHeight > 150000000) {
     throw Error("预计成图过大，请减少行列或缩略图宽度。");
   }
   canvas.style.setProperty("--canvas-width", canvasWidth + "px");
   canvas.style.setProperty("--canvas-ratio", canvasWidth / canvasHeight);
   let paintedWidth = 0;
   let paintedHeight = 0;

   function paint() {
     const displayWidth = canvas.getBoundingClientRect().width;
     if (!displayWidth) return;
     // Match the displayed pixels, with limits for unusually large compositions.
     const scale = Math.min(displayWidth * window.devicePixelRatio / canvasWidth,
       8192 / Math.max(canvasWidth, canvasHeight),
       Math.sqrt(8_000_000 / (canvasWidth * canvasHeight)));
     const pixelWidth = Math.max(1, Math.floor(canvasWidth * scale));
     const pixelHeight = Math.max(1, Math.floor(canvasHeight * scale));
     if (pixelWidth === paintedWidth && pixelHeight === paintedHeight) return;
     paintedWidth = canvas.width = pixelWidth;
     paintedHeight = canvas.height = pixelHeight;
     ctx.setTransform(pixelWidth / canvasWidth, 0, 0, pixelHeight / canvasHeight, 0, 0);
     ctx.imageSmoothingQuality = "high";
     ctx.fillStyle = "#fff";
     ctx.fillRect(0, 0, canvasWidth, canvasHeight);
     if (headerHeight) {
       ctx.fillStyle = "#263229";
       ctx.font = fontStyle;
       ctx.textBaseline = "middle";
       const top = (headerHeight - lines.length * lineHeight) / 2;
       lines.forEach((line, index) => ctx.fillText(line, 8, top + (index + .5) * lineHeight));
       ctx.textBaseline = "alphabetic";
     }
     const cover = !single && fit === "cover";
     const zoom = (cover ? Math.max : Math.min)(width / scenery.width, height / scenery.height);
     const artWidth = scenery.width * zoom;
     const artHeight = scenery.height * zoom;
     for (let index = 0; index < (single ? 1 : count); index++) {
       const x = gap + (styled ? 3 : 0) + (index % columns) * cellWidth;
       const y = headerHeight + gap + (styled ? 3 : 0) + Math.floor(index / columns) * cellHeight;
       ctx.save();
       if (styled) {
         ctx.shadowColor = "#17291e60";
         ctx.shadowBlur = ctx.shadowOffsetX = ctx.shadowOffsetY = 3 * scale;
       }
       ctx.fillStyle = "#000";
       ctx.fillRect(x, y, width, height + timestampBar);
       ctx.restore();
       ctx.save();
       ctx.beginPath();
       ctx.rect(x, y, width, height);
       ctx.clip();
       ctx.drawImage(scenery, x + (width - artWidth) / 2, y + (height - artHeight) / 2, artWidth, artHeight);
       ctx.restore();
       if (styled) {
         ctx.fillStyle = "#fff";
         ctx.font = "18px sans-serif";
         ctx.textAlign = "center";
         ctx.fillText(fmt(Math.floor(times[single ? frame : index])), x + width / 2, y + height + 21);
       }
     }
   }

   detail.style.font = fontStyle;
   detail.style.lineHeight = lineHeight + "px";
   detail.replaceChildren(...lines.map(text => {
     const line = document.createElement("div");
     line.textContent = text;
     return line;
   }));

   return {
     width, height, canvasWidth, canvasHeight, count, rows, headerHeight,
     infoRows: lines.length, heightLimited: headerHeight > targetHeight, paint
   };
 }

 function previewValues(values) {
   const [start, end] = trims[values.collage_default_content_type] ||
     [values.collage_custom_skip_start, values.collage_custom_skip_end];
   return {
     single: values.screenshot_output_mode !== "collage",
     style: values.screenshot_default_style,
     format: values.screenshot_default_format,
     count: Number(values.collage_default_total),
     rows: Number(values.collage_default_rows),
     cols: Number(values.collage_default_columns),
     tileWidth: Number(values.collage_default_width),
     singleWidth: Number(values.screenshot_default_width),
     gap: Number(values.collage_default_gap),
     layout: values.collage_default_layout_mode,
     ratio: values.collage_ratio,
     fit: values.collage_fit,
     info: values.collage_info_enabled,
     font: Number(values.collage_info_size),
     family: values.collage_info_font,
     lines: Number(values.collage_info_rows),
     pct: Number(values.collage_info_percent),
     fields: values.collage_info_fields || [],
     sampling: values.screenshot_default_sampling,
     times: values.screenshot_default_timestamps || [],
     start: Number(start),
     end: Number(end)
   };
 }

 function mount(sections, getValues, onTab, selectedTab) {
   const root = document.createElement("div");
   root.className = "screenshot-settings";
   const controls = document.createElement("div");
   const tabs = document.createElement("nav");
   tabs.className = "screenshot-settings-tabs";
   tabs.setAttribute("aria-label", "截图参数");
   const buttons = [];
   const view = document.createElement("aside");
   view.className = "screenshot-preview";
   view.innerHTML = `
     <div class="screenshot-preview-head">
       <div><h3>缩略图布局预览</h3><small class="muted">风景示意素材 · 源画面 3840 × 1608</small></div>
       <button type="button" class="button secondary small" data-zoom>查看原寸</button>
     </div>
     <figure class="screenshot-preview-figure"><canvas role="img" aria-label="缩略图布局示意"></canvas></figure>
     <div class="screenshot-preview-size" aria-live="polite">
       <span data-tile></span><strong data-size></strong>
     </div>
     <p class="muted" data-measure></p>
     <div class="screenshot-preview-nav">
       <button type="button" aria-label="上一张" class="button secondary small" data-prev>‹</button>
       <span data-page></span>
       <button type="button" aria-label="下一张" class="button secondary small" data-next>›</button>
     </div>
     <section class="screenshot-info-sample"><h4>信息栏原寸文字</h4><div tabindex="0"></div></section>
     <p class="muted" role="status" data-hint></p>
   `;
   root.append(controls, view);
   controls.append(tabs);
   const q = selector => view.querySelector(selector);
   const figure = q("figure");
   const canvas = q("canvas");
   const sampleText = q(".screenshot-info-sample");
   const pager = q(".screenshot-preview-nav");
   const sizes = q(".screenshot-preview-size");
   const zoom = q("[data-zoom]");
   const measure = q("[data-measure]");
   let frame = 0;
   let layout = null;
   const repaint = () => layout?.paint();
   const observer = new ResizeObserver(repaint);
   observer.observe(canvas);
   window.addEventListener("resize", repaint);

   function select(tab) {
     if (tab === "information" && getValues().screenshot_output_mode !== "collage") tab = "layout";
     for (const {button, key, nodes} of buttons) {
       button.setAttribute("aria-pressed", String(key === tab));
       nodes.forEach(node => { node.hidden = key !== tab; });
     }
     selectedTab = tab;
     onTab(tab);
   }

   for (const [key, title, sectionKeys] of [
     ["layout", "布局尺寸", ["layout"]],
     ["information", "信息栏", ["information"]],
     ["sampling", "取帧", ["sampling"]],
     ["output", "输出", ["rendering", "output"]]
   ]) {
     const button = document.createElement("button");
     button.type = "button";
     button.className = "button ghost small";
     button.textContent = title;
     const nodes = sectionKeys.map(name => sections.find(section => section.dataset.section === name)).filter(Boolean);
     if (!nodes.length) continue;
     button.onclick = () => select(key);
     tabs.append(button);
     controls.append(...nodes);
     buttons.push({button, key, nodes});
   }

   function update() {
     const options = previewValues(getValues());
     const infoButton = buttons.find(item => item.key === "information");
     if (infoButton) infoButton.button.disabled = options.single;
     if (options.single && selectedTab === "information") select("layout");
     try {
       const times = sampleTimes(options);
       frame = Math.min(frame, times.length - 1);
       layout = compose(canvas, options, frame, sampleText.querySelector("div"), times);
     } catch (error) {
       layout = null;
       figure.hidden = sizes.hidden = sampleText.hidden = pager.hidden = measure.hidden = true;
       zoom.disabled = true;
       q("[data-hint]").textContent = error.message;
       return;
     }
     figure.hidden = sizes.hidden = measure.hidden = zoom.disabled = false;
     repaint();
     sampleText.hidden = !layout.headerHeight;
     pager.hidden = !options.single;
     q("[data-tile]").textContent = (options.single ? "单张画面 " : "单格画面 ") + `${layout.width} × ${layout.height} px`;
     q("[data-size]").textContent = `预计成图 ${layout.canvasWidth} × ${layout.canvasHeight} px`;
     const caption = options.single
       ? `独立截图 ${layout.count} 张 · ${options.format.toUpperCase()} · ${options.style === "plain" ? "纯画面" : "时间栏与阴影"}`
       : `${options.cols} 列 × ${layout.rows} 行 · ${layout.count} 帧`;
     measure.textContent = caption + (layout.headerHeight
       ? ` · 信息栏 ${layout.infoRows} 行 · 占整图 ${(100 * layout.headerHeight / layout.canvasHeight).toFixed(1)}%`
       : "");
     const hints = [];
     if (layout.heightLimited) hints.push("字号与行数需要更多高度，已保留完整文字。");
     if (layout.headerHeight && layout.infoRows > options.lines) hints.push("当前宽度需要额外换行。");
     if (layout.headerHeight && layout.infoRows < options.lines) hints.push(`按已选字段显示 ${layout.infoRows} 行。`);
     q("[data-hint]").textContent = hints.join(" ");
     q("[data-page]").textContent = `${frame + 1} / ${layout.count}`;
     q("[data-prev]").disabled = frame === 0;
     q("[data-next]").disabled = frame + 1 === layout.count;
   }

   zoom.onclick = () => {
     zoom.textContent = figure.classList.toggle("is-native") ? "适应预览" : "查看原寸";
     repaint();
   };
   q("[data-prev]").onclick = () => { frame--; update(); };
   q("[data-next]").onclick = () => { frame++; update(); };
   select(selectedTab);
   return {root, update, select, destroy() {
     observer.disconnect();
     window.removeEventListener("resize", repaint);
   }};
 }

 function fields(value, onChange) {
   const wrapper = document.createElement("div");
   wrapper.className = "settings-field";
   wrapper.dataset.settingKey = "collage_info_fields";
   const heading = document.createElement("strong");
   heading.textContent = "字段与顺序";
   const list = document.createElement("div");
   list.className = "screenshot-fields";
   wrapper.append(heading, list);
   let selected = value.slice();

   function render(focusKey, direction) {
     const keys = [...selected, ...Object.keys(labels).filter(key => !selected.includes(key))];
     list.replaceChildren();
     for (const key of keys) {
       const row = document.createElement("div");
       const label = document.createElement("label");
       const input = document.createElement("input");
       input.type = "checkbox";
       const index = selected.indexOf(key);
       input.checked = index !== -1;
       input.onchange = () => {
         selected = input.checked ? [...selected, key] : selected.filter(value => value !== key);
         onChange(selected);
         render(key);
       };
       label.append(input, labels[key]);
       const actions = document.createElement("span");
       const move = [-1, 1].map(offset => {
         const button = document.createElement("button");
         button.type = "button";
         button.className = "button ghost small";
         button.textContent = offset < 0 ? "↑" : "↓";
         button.setAttribute("aria-label", (offset < 0 ? "上移" : "下移") + labels[key]);
         button.disabled = index < 0 || index + offset < 0 || index + offset >= selected.length;
         button.onclick = () => {
           const order = selected.slice();
           [order[index], order[index + offset]] = [order[index + offset], order[index]];
           selected = order;
           onChange(selected);
           render(key, offset);
         };
         actions.append(button);
         return button;
       });
       row.append(label, actions);
       list.append(row);
       if (key === focusKey) {
         const target = direction && move[direction < 0 ? 0 : 1];
         (target && !target.disabled ? target : input).focus();
       }
     }
   }
   render();
   return wrapper;
 }

 window.ScreenshotLayoutSettings = {mount, fields};
})();
