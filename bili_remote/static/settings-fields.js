(function () {
  "use strict";

  function node(tag, className, text) {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (text !== undefined && text !== null) item.textContent = String(text);
    return item;
  }

  function button(text, className = "button ghost small") {
    const item = node("button", className, text);
    item.type = "button";
    return item;
  }

  function markBusy(target, busy) {
    target.disabled = busy;
    target.classList.toggle("is-busy", busy);
    target.setAttribute("aria-busy", busy ? "true" : "false");
  }

  function fieldLabel(field) {
    const label = node("label", "settings-field");
    label.dataset.settingKey = field.key;
    const heading = node("span", "settings-field-label", field.label);
    if (field.advanced) heading.append(node("span", "settings-advanced-badge", "高级"));
    label.append(heading);
    if (field.description) label.append(node("small", "settings-field-help", field.description));
    return label;
  }

  function appendChoices(control, field) {
    for (const choice of field.choices || []) {
      const option = node("option", null, choice.label);
      option.value = choice.value;
      control.append(option);
    }
  }

  function inputControl(field, value, onChange) {
    const label = fieldLabel(field);
    let input;
    if (field.control === "select") {
      input = document.createElement("select");
      appendChoices(input, field);
      input.value = value ?? field.default ?? "";
    } else if (field.control === "number") {
      input = document.createElement("input");
      input.type = "number";
      input.inputMode = field.step && !Number.isInteger(field.step) ? "decimal" : "numeric";
      if (field.minimum !== undefined) input.min = field.minimum;
      if (field.maximum !== undefined) input.max = field.maximum;
      if (field.step !== undefined) input.step = field.step;
      input.value = value ?? field.default ?? "";
    } else if (field.control === "list") {
      input = document.createElement("textarea");
      input.rows = 4;
      input.value = Array.isArray(value) ? value.join("\n") : String(value || "");
      input.placeholder = field.placeholder || "每行一个";
    } else if (field.control === "password") {
      input = document.createElement("input");
      input.type = "password";
      input.autocomplete = "new-password";
      input.value = value ?? "";
      input.placeholder = field.placeholder || "输入新值";
    } else {
      input = document.createElement("input");
      input.value = value ?? field.default ?? "";
      input.placeholder = field.placeholder || "";
      if (field.key === "rename_default_source" && field.choices?.length) {
        input.setAttribute("list", "settings-source-values");
      }
    }
    if (field.control === "template") input.classList.add("monospace-input");
    if (field.control === "number") input.inputMode = input.inputMode || "numeric";
    input.addEventListener("input", () => {
      if (field.control === "number") {
        onChange(input.value === "" ? "" : Number(input.value));
      } else if (field.control === "list") {
        onChange(input.value.split(/[\r\n,]+/).map(item => item.trim()).filter(Boolean));
      } else {
        onChange(input.value);
      }
    });
    input.addEventListener("change", () => {
      if (field.control === "select") onChange(input.value);
    });
    label.append(input);
    if (field.sensitive && field.configured) {
      label.append(node("small", "settings-field-help", "已保存凭据；留空保持现有值，输入新值后保存会替换。"));
    }
    if (field.unit) label.append(node("small", "settings-field-help", field.unit));
    return label;
  }

  function switchControl(field, value, onChange) {
    const label = node("label", "settings-field settings-switch-field");
    label.dataset.settingKey = field.key;
    const text = node("span", "settings-switch-copy");
    const heading = node("strong", null, field.label);
    if (field.advanced) heading.append(node("span", "settings-advanced-badge", "高级"));
    text.append(heading);
    if (field.description) text.append(node("small", "settings-field-help", field.description));
    const input = document.createElement("input");
    input.type = "checkbox";
    input.checked = Boolean(value);
    input.addEventListener("change", () => onChange(input.checked));
    label.append(text, input);
    return label;
  }

  function readonlyControl(field, value) {
    const row = node("div", "settings-readonly-row");
    const copy = node("div");
    copy.append(node("strong", null, field.label));
    if (field.description) copy.append(node("small", "settings-field-help", field.description));
    row.append(copy, node("span", "settings-readonly-value", value ?? "—"));
    return row;
  }

  function directoryControl(field, directory, mountRoots, onChange, onError) {
    const wrapper = node("div", "settings-directory-row");
    wrapper.dataset.settingKey = field.key;
    const heading = node("div", "settings-directory-heading");
    const copy = node("div");
    copy.append(node("strong", null, field.label));
    copy.append(node("small", "muted", field.description || ""));
    const badge = node("span", "settings-badge");
    if (directory?.available) {
      badge.textContent = directory.writable ? "可读写" : "不可写";
      badge.classList.add(directory.writable ? "ok" : "warning");
    } else {
      badge.textContent = "不存在";
      badge.classList.add("invalid");
    }
    heading.append(copy, badge);

    const effective = node("div", "settings-directory-effective");
    const mount = mountRoots.find(item => item.key === directory?.mount_key);
    effective.append(
      node("small", "muted", `挂载根：${mount?.label || directory?.mount_key || "—"}`),
      node("code", null, directory?.path || "—")
    );
    const actions = node("div", "settings-directory-actions");
    const browse = button("浏览选择", "button secondary small");
    browse.addEventListener("click", async () => {
      const error = wrapper.querySelector(".form-error");
      if (error) error.textContent = "";
      markBusy(browse, true);
      try {
        const selected = await FileBrowser.openPicker({
          mode: "settings-directory",
          scope: "mounts",
          rootKey: directory?.mount_key || "downloads",
          path: directory?.relative_path || "",
          selectionType: "directory",
          title: `选择${field.label}`,
          confirmLabel: "使用此目录"
        });
        if (selected) {
          onChange({mount_key: selected.root_key, relative_path: selected.relative_path});
          const selectedMount = mountRoots.find(item => item.key === selected.root_key);
          const selectedPath = [selectedMount?.path, selected.relative_path].filter(Boolean).join("/");
          effective.querySelector("code").textContent = selectedPath || "挂载根目录";
          effective.querySelector("small").textContent = `挂载根：${selectedMount?.label || selected.root_key}`;
        }
      } catch (error) {
        if (onError) onError(error.message);
      } finally {
        markBusy(browse, false);
      }
    });
    actions.append(browse);
    wrapper.append(heading, effective, actions);
    return wrapper;
  }

  function relativeDirectoryControl(field, value, onChange, onError) {
    const label = fieldLabel(field);
    const row = node("div", "input-action");
    const rootKey = field.directory_root || "downloads";
    const input = document.createElement("input");
    input.value = value || "";
    input.placeholder = field.placeholder || "留空表示下载根目录";
    input.addEventListener("input", () => onChange(input.value));
    const browse = button("选择目录", "button ghost");
    browse.addEventListener("click", async () => {
      markBusy(browse, true);
      try {
        const selected = await FileBrowser.openPicker({
          mode: "settings-directory",
          scope: "browse",
          rootKeys: [rootKey],
          rootKey,
          path: input.value.trim(),
          selectionType: "directory",
          title: `选择${field.label}`,
          confirmLabel: "使用此目录"
        });
        if (selected) {
          input.value = selected.relative_path;
          onChange(selected.relative_path);
        }
      } catch (error) {
        if (onError) onError(error.message);
      } finally {
        markBusy(browse, false);
      }
    });
    row.append(input, browse);
    label.append(row);
    return label;
  }

  function renderField(field, value, context) {
    if (field.control === "readonly") return readonlyControl(field, value);
    if (field.control === "switch") return switchControl(field, value, context.onChange);
    if (field.control === "directory") {
      const key = field.key.replace(/^directories\./, "");
      return directoryControl(
        field,
        context.directories?.[key],
        context.mountRoots || [],
        selected => context.onDirectoryChange(key, selected),
        context.onError
      );
    }
    if (field.control === "relative-directory") {
      return relativeDirectoryControl(field, value, context.onChange, context.onError);
    }
    const enriched = field.sensitive
      ? {
          ...field,
          configured: Boolean(
            field.key === "qbittorrent_password"
              ? context.secretsConfigured?.qbittorrent_credentials
              : context.secretsConfigured?.[field.key]
          )
        }
      : field;
    return inputControl(enriched, value, context.onChange);
  }

  window.SettingsFields = {renderField};
})();
