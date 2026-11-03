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

  function markBusy(target, active) {
    target.disabled = active;
    target.classList.toggle("is-busy", active);
    target.setAttribute("aria-busy", active ? "true" : "false");
  }

  class NamingProfilesEditor {
    constructor(host) {
      this.host = host;
      this.profiles = [];
      this.editId = null;
      this.status = node("p", "file-action-status");
      this.status.setAttribute("role", "status");
      this.list = node("div", "naming-profile-list");
      this.build();
    }

    build() {
      const form = node("form", "stack naming-profile-form");
      const nameField = node("label");
      nameField.append(node("span", null, "名称"));
      this.name = document.createElement("input");
      this.name.maxLength = 120;
      this.name.required = true;
      nameField.append(this.name);

      const typeField = node("label");
      typeField.append(node("span", null, "媒体类型"));
      this.type = document.createElement("select");
      this.type.append(node("option", null, "剧集"), node("option", null, "电影"));
      this.type.options[0].value = "series";
      this.type.options[1].value = "movie";
      typeField.append(this.type);
      const two = node("div", "settings-two-columns");
      two.append(nameField, typeField);

      const templateField = node("label");
      templateField.append(node("span", null, "模板"));
      this.template = document.createElement("input");
      this.template.className = "monospace-input";
      this.template.required = true;
      templateField.append(this.template);

      const defaultLabel = node("label", "check-row");
      this.isDefault = document.createElement("input");
      this.isDefault.type = "checkbox";
      defaultLabel.append(this.isDefault, node("span", null, "设为该媒体类型的默认预设"));

      this.error = node("p", "form-error");
      this.error.setAttribute("role", "alert");
      const actions = node("div", "button-row");
      this.saveButton = button("新增预设", "button primary");
      this.saveButton.type = "submit";
      this.cancelButton = button("取消编辑", "button ghost hidden");
      this.cancelButton.addEventListener("click", () => this.resetForm());
      actions.append(this.saveButton, this.cancelButton);
      form.append(two, templateField, defaultLabel, this.error, actions);
      form.addEventListener("submit", event => this.submit(event));

      this.host.append(this.status, this.list, form);
      this.load();
    }

    async load() {
      try {
        this.profiles = await api("/api/naming/profiles");
        this.render();
      } catch (error) {
        this.status.textContent = `命名预设读取失败：${error.message}`;
        this.status.classList.add("error");
      }
    }

    render() {
      this.list.replaceChildren();
      if (!this.profiles.length) {
        this.list.append(node("p", "empty", "还没有命名预设"));
        return;
      }
      for (const profile of this.profiles) {
        const row = node("div", "naming-profile-row");
        const info = node("div", "naming-profile-info");
        const heading = node("div", "naming-profile-heading");
        heading.append(node("strong", null, profile.name));
        heading.append(node("span", "settings-badge", profile.media_type === "series" ? "剧集" : "电影"));
        if (profile.is_default) heading.append(node("span", "settings-badge ok", "默认"));
        info.append(heading, node("code", "naming-profile-template", profile.template));
        const actions = node("div", "naming-profile-actions");
        const edit = button("编辑");
        const makeDefault = button("设为默认");
        const remove = button("删除", "button danger small");
        edit.addEventListener("click", () => this.edit(profile));
        makeDefault.disabled = Boolean(profile.is_default);
        makeDefault.addEventListener("click", () => this.setDefault(profile, makeDefault));
        remove.addEventListener("click", () => this.remove(profile, remove));
        actions.append(edit, makeDefault, remove);
        row.append(info, actions);
        this.list.append(row);
      }
    }

    edit(profile) {
      this.editId = profile.id;
      this.name.value = profile.name;
      this.type.value = profile.media_type;
      this.type.disabled = true;
      this.template.value = profile.template;
      this.isDefault.checked = Boolean(profile.is_default);
      this.saveButton.textContent = "保存修改";
      this.cancelButton.classList.remove("hidden");
      this.error.textContent = "";
      this.name.focus();
    }

    resetForm() {
      this.editId = null;
      this.name.value = "";
      this.type.value = "series";
      this.type.disabled = false;
      this.template.value = "";
      this.isDefault.checked = false;
      this.saveButton.textContent = "新增预设";
      this.cancelButton.classList.add("hidden");
      this.error.textContent = "";
    }

    payload() {
      return {
        name: this.name.value.trim(),
        template: this.template.value.trim(),
        is_default: this.isDefault.checked
      };
    }

    async submit(event) {
      event.preventDefault();
      this.error.textContent = "";
      this.status.textContent = "";
      markBusy(this.saveButton, true);
      try {
        const body = this.payload();
        if (this.editId) {
          await api(`/api/naming/profiles/${this.editId}`, {
            method: "PATCH",
            body: JSON.stringify(body)
          });
        } else {
          await api("/api/naming/profiles", {
            method: "POST",
            body: JSON.stringify({...body, media_type: this.type.value})
          });
        }
        this.resetForm();
        await this.load();
        this.status.textContent = "命名预设已保存";
      } catch (error) {
        this.error.textContent = error.message;
      } finally {
        markBusy(this.saveButton, false);
      }
    }

    async setDefault(profile, target) {
      this.error.textContent = "";
      this.status.textContent = `正在将“${profile.name}”设为默认…`;
      if (target) markBusy(target, true);
      try {
        await api(`/api/naming/profiles/${profile.id}`, {
          method: "PATCH",
          body: JSON.stringify({
            name: profile.name,
            template: profile.template,
            is_default: true
          })
        });
        await this.load();
        this.status.textContent = "默认命名预设已更新";
      } catch (error) {
        this.error.textContent = error.message;
        this.status.textContent = "";
      } finally {
        if (target) markBusy(target, false);
      }
    }

    async remove(profile, target) {
      const message = profile.is_default
        ? `删除默认命名预设“${profile.name}”？系统会自动选择同类型的剩余预设作为默认项。`
        : `删除命名预设“${profile.name}”？`;
      if (!confirm(message)) return;
      this.error.textContent = "";
      this.status.textContent = `正在删除“${profile.name}”…`;
      if (target) markBusy(target, true);
      try {
        await api(`/api/naming/profiles/${profile.id}`, {method: "DELETE"});
        if (this.editId === profile.id) this.resetForm();
        await this.load();
        this.status.textContent = "命名预设已删除";
      } catch (error) {
        this.error.textContent = error.message;
        this.status.textContent = "";
      } finally {
        if (target) markBusy(target, false);
      }
    }
  }

  window.NamingProfilesEditor = NamingProfilesEditor;
})();
