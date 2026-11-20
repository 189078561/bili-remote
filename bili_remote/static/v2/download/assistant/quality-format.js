// Shared by the project workbench and the packaged browser extension.
(() => {
  function codecName(value) {
    const text = String(value || "").toLowerCase();
    if (text.includes("avc") || text === "h264") return "H.264";
    if (text.includes("hev") || text.includes("hvc") || text === "h265") return "H.265";
    if (text.includes("av01") || text === "av1") return "AV1";
    if (text.includes("mp4a")) return "AAC";
    if (text.includes("flac")) return "FLAC";
    if (text.includes("opus")) return "Opus";
    if (text.includes("ac-3") || text.includes("ec-3")) return "Dolby";
    return "未知编码";
  }

  function formatBitrate(kbps) {
    const value = Number(kbps);
    if (!Number.isFinite(value) || value <= 0) return null;
    return value >= 1000 ? `${(value / 1000).toFixed(2)} Mbps` : `${Math.round(value)} kbps`;
  }

  function formatResourceSize(bytes) {
    const value = Number(bytes);
    if (!Number.isFinite(value) || value <= 0) return null;
    if (value >= 1_000_000_000) return `${(value / 1_000_000_000).toFixed(2)} GB`;
    return value < 100_000 ? "<0.1 MB" : `${(value / 1_000_000).toFixed(1)} MB`;
  }

  function displayResolution(track) {
    if (!track.height) return "仅音频";
    const nominal = {6: 240, 16: 360, 32: 480, 64: 720, 74: 720,
      80: 1080, 112: 1080, 116: 1080, 120: 2160, 127: 4320}[track.quality];
    if (nominal) return nominal + "p";
    return track.width ? track.width + " × " + track.height : track.height + " 像素高";
  }

  function frameRate(track) {
    const fps = Math.round(Number(track.fps));
    return fps > 0 ? fps + (track.quality ? " fps" : "fps") : null;
  }

  function trackFamily(track) {
    const codec = track.vcodec && track.vcodec !== "none" ? track.vcodec : track.acodec;
    const name = codecName(codec);
    return JSON.stringify([name === "未知编码" ? codec : name, track.dynamic_range || "", Math.round(Number(track.fps)) || 0]);
  }

  function trackTier(track) {
    return JSON.stringify([track.format_id, track.quality || Number(track.height) || 0, trackFamily(track)]);
  }

  function bitrateRange(values) {
    const rates = values.map(Number).filter(rate => Number.isFinite(rate) && rate > 0);
    if (!rates.length) return "码率未知";
    const low = Math.min(...rates), high = Math.max(...rates);
    const label = low === high ? formatBitrate(low) :
      low >= 1000 ? (low / 1000).toFixed(2) + "–" + formatBitrate(high) :
      high < 1000 ? Math.round(low) + "–" + formatBitrate(high) :
      formatBitrate(low) + "–" + formatBitrate(high);
    return label + (rates.length < values.length ? "（部分未知）" : "");
  }

  function combinedSize(rows) {
    const known = rows.filter(row => Number(row.track?.size_with_audio) > 0);
    return {
      bytes: known.reduce((sum, row) => sum + Number(row.track.size_with_audio), 0),
      estimated: known.some(row => row.track.size_estimated),
      complete: known.length === rows.length && rows.length > 0
    };
  }

  function batchTrackLabel(track, tier, rate) {
    const video = track.height && track.vcodec !== "none";
    return [
      video ? (track.quality ? displayResolution(track) : track.height + "P") : "仅音频",
      video ? frameRate(track) : null,
      codecName(video ? track.vcodec : track.acodec), tier?.label,
      video ? track.dynamic_range : null, rate
    ].filter(Boolean).join(" · ");
  }

  function tierCatalogue(tracks, target) {
    const tiers = new Map();
    for (const track of tracks) {
      const key = trackTier(track);
      if (!tiers.has(key)) tiers.set(key, {key, track, rates: []});
      tiers.get(key).rates.push(track.tbr);
    }
    if (target && !tiers.has(target.key)) {
      tiers.set(target.key, {...target, rates: []});
    }
    const highest = tier => Math.max(0, Number(tier.track.tbr) || 0, ...tier.rates.map(Number).filter(Number.isFinite));
    const ordered = [...tiers.values()].sort((a, b) =>
      (Number(b.track.height) || 0) - (Number(a.track.height) || 0) || highest(b) - highest(a)
    );
    for (const tier of ordered) {
      const track = tier.track;
      const group = ordered.filter(other => (other.track.quality || other.track.height) === (track.quality || track.height) && trackFamily(other.track) === trackFamily(track));
      if (group.length > 1) {
        const rank = group.indexOf(tier);
        tier.label = rank === 0 ? "高码" : rank === 1 ? "标准" : "档位 " + (rank + 1);
      }
    }
    return ordered;
  }

  function preferredTier(catalogue, trackGroups) {
    const video = catalogue.filter(tier => tier.track.height > 0);
    const candidates = video.length ? video : catalogue;
    return candidates.find(tier =>
      trackGroups.every(tracks => tracks.some(track => trackTier(track) === tier.key))
    ) || candidates[0];
  }

  globalThis.BiliDownloadQuality = Object.freeze({displayResolution, frameRate, codecName, formatBitrate, formatResourceSize, trackFamily, trackTier, bitrateRange, combinedSize, batchTrackLabel, tierCatalogue, preferredTier});
})();
