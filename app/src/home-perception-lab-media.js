/* Bounded binary media adapter. URLs, credentials and storage paths never enter the player. */
(function (global) {
  'use strict';

  const CHUNK = 4 * 1024 * 1024,
    MAX_OBJECT = 32 * 1024 * 1024,
    OPEN_TIMEOUT_MS = 15000;
  // Transport and gateway admit only these image types.
  const FRAME_TYPES = ['image/jpeg', 'image/png'];
  // Status-less reads that never reached or never finished at the service: the
  // browser network/15 s timeout, and native permit-cap or send/body failures.
  const TRANSIENT_CODES = ['lab_unavailable', 'lab_transport_busy', 'lab_upstream_unavailable'];
  const FETCH_TYPE_ERROR = /^(failed to fetch|fetch failed|load failed|network ?error when attempting to fetch resource\.?|network request failed)$/i;
  function transientFrameError(error) {
    const status=error?.status||error?.statusCode||0;
    // Auth, missing, conflicting and deleted media are answers, not outages.
    if(status>=400&&status<500&&status!==408&&status!==429)return false;
    if([408,429,502,503,504].includes(status))return true;
    // A programming TypeError is not a network failure.
    if(error?.name==='TypeError')return FETCH_TYPE_ERROR.test(error.message||'');
    return (!status&&TRANSIENT_CODES.includes(error?.code))||/network|fetch|timeout|timed out|incomplete media/i.test(error?.message||'');
  }
  async function readFrame(frame, context, signal, onAttempt=()=>{}, wait) {
    const sleep=wait||((ms)=>new Promise((resolve,reject)=>{
      if(signal?.aborted)return reject(new DOMException('Cancelled','AbortError'));
      const cancel=()=>{clearTimeout(timer);reject(new DOMException('Cancelled','AbortError'));};
      const timer=setTimeout(()=>{signal?.removeEventListener('abort',cancel);resolve();},ms);
      signal?.addEventListener('abort',cancel,{once:true});
    }));
    for(let attempt=1;attempt<=3;attempt++) {
      if(signal?.aborted)throw new DOMException('Cancelled','AbortError');
      onAttempt(attempt);
      try {
        const item=frame.size_bytes||frame.bytes?frame:await global.HomePerceptionLabTransport.request('media.describe',
          {...context,mediaId:frame.media_id},{signal});
        const type=item.content_type||'image/jpeg';
        if(!FRAME_TYPES.includes(type))throw Object.assign(Error('Unsupported frame format'),{permanent:true,code:'frame_type_unsupported'});
        return {bytes:await readObject(item,context,signal),type};
      } catch(error) {
        if(signal?.aborted||error?.name==='AbortError'||error?.permanent||!transientFrameError(error)||attempt===3)throw error;
        await sleep(attempt===1?200:600);
      }
    }
  }
  async function readObject(item, context, signal) {
    const size = item.size_bytes ?? item.bytes;
    if (!Number.isSafeInteger(size) || size <= 0 || size > MAX_OBJECT) throw Error('Media object exceeds the supported preview bound');
    const bytes = new Uint8Array(size);
    for (let offset = 0; offset < size; offset += CHUNK) {
      if (signal?.aborted) throw new DOMException('Cancelled', 'AbortError');
      const length = Math.min(CHUNK, size - offset);
      const value = await global.HomePerceptionLabTransport.requestMedia({
        siteId: context.siteId,
        sessionId: context.sessionId,
        mediaId: item.media_id || item.id,
        generation: context.generation,
        offset,
        length
      }, {
        signal
      });
      if (value.data?.length !== length) throw Error('Incomplete media object');
      bytes.set(value.data, offset);
    }
    return bytes;
  }
  function videoItems(manifest) {
    return (manifest?.items || []).filter(x => (x.content_type || '').startsWith('video/mp4')).sort((a, b) => (a.metadata?.role === 'init' ? -1 : a.metadata?.segment_index ?? 0) - (b.metadata?.role === 'init' ? -1 : b.metadata?.segment_index ?? 0));
  }
  function createPlayer(video, context, onStatus) {
    let manifest = null,
      source = null,
      buffer = null,
      url = null,
      dead = false,
      busy = false,
      appended = new Map(),
      abort = new AbortController(),
      seekTarget = null,
      openTimer = null;
    const contains = t => {
      if (!buffer) return false;
      for (let i = 0; i < buffer.buffered.length; i++) if (buffer.buffered.start(i) <= t && buffer.buffered.end(i) > t) return true;
      return false;
    };
    const bufferOperation = operation => new Promise((resolve, reject) => {
      const activeBuffer = buffer;
      let timeout;
      const finish = error => {
        clearTimeout(timeout);
        activeBuffer.removeEventListener('updateend', done);
        activeBuffer.removeEventListener('error', bad);
        abort.signal.removeEventListener('abort', cancelled);
        error ? reject(error) : resolve();
      };
      const done = () => finish();
      const bad = () => finish(Error('Video segment was rejected by the decoder'));
      const cancelled = () => finish(new DOMException('Cancelled', 'AbortError'));
      activeBuffer.addEventListener('updateend', done, {once:true});
      activeBuffer.addEventListener('error', bad, {once:true});
      abort.signal.addEventListener('abort', cancelled, {once:true});
      timeout = setTimeout(() => finish(Error('Video decoder did not finish within 10 seconds')), 10000);
      if (abort.signal.aborted) return cancelled();
      try { operation(); } catch (error) { finish(error); }
    });
    async function pump() {
      if (dead || busy || !buffer || buffer.updating) return;
      busy = true;
      let appendedSuccessfully = false;
      try {
        const items = videoItems(manifest),
          at = seekTarget ?? video.currentTime ?? 0;
        const next = items.find(item => {
          if (appended.has(item.id || item.media_id)) return false;
          const m = item.metadata || {};
          if (m.role === 'init') return true;
          const start = Number(m.timeline_start_ms != null ? m.timeline_start_ms / 1000 : m.source_start_s ?? m.start_seconds ?? 0);
          const end = Number(m.timeline_end_ms != null ? m.timeline_end_ms / 1000 : m.source_end_s ?? m.end_seconds ?? start + 30);
          return end >= at - 2 && start < at + 20;
        });
        if (!next) {
          const track = manifest?.encoded_track;
          if (track?.finalized === true && typeof track.final_media_id === 'string' &&
              appended.has(track.final_media_id) && source?.readyState === 'open') {
            source.endOfStream();
          }
          if (seekTarget != null && contains(seekTarget)) seekTarget = null;
          onStatus?.({
            state: contains(at) || video.ended ? 'ready' : 'unavailable',
            reason: contains(at) || video.ended ? '' : 'No decoded video at the selected time'
          });
          return;
        }
        const data = await readObject(next, {
          ...context,
          generation: manifest.generation
        }, abort.signal);
        if (dead) return;
        if (buffer.buffered.length && at > 35) {
          const end = Math.min(at - 30, buffer.buffered.end(0));
          if (end > buffer.buffered.start(0)) {
            await bufferOperation(() => buffer.remove(buffer.buffered.start(0), end));
            for (const [id, itemEnd] of appended) {
              if (Number.isFinite(itemEnd) && itemEnd <= end) appended.delete(id);
            }
          }
        }
        if (buffer.buffered.length) {
          const last = buffer.buffered.length - 1,
            start = Math.max(at + 40, buffer.buffered.start(last)),
            end = buffer.buffered.end(last);
          if (end > start) {
            await bufferOperation(() => buffer.remove(start, end));
            for (const [id, itemEnd] of appended) {
              if (Number.isFinite(itemEnd) && itemEnd > start) appended.delete(id);
            }
          }
        }
        const m = next.metadata || {};
        if (m.reset_timestamps === true) {
          const offset = m.timeline_start_ms != null ? m.timeline_start_ms / 1000 : m.source_start_s;
          if (!Number.isFinite(offset)) throw Error('Segment timeline mapping is unavailable');
          buffer.timestampOffset = offset;
        }
        await bufferOperation(() => buffer.appendBuffer(data));
        if (dead) return;
        appended.set(next.id || next.media_id, m.role === 'init' ? Infinity : Number(m.timeline_end_ms != null ? m.timeline_end_ms / 1000 : m.source_end_s ?? m.end_seconds));
        appendedSuccessfully = true;
        if (seekTarget != null && contains(seekTarget)) {
          video.currentTime = seekTarget;
          seekTarget = null;
        }
        onStatus?.({
          state: contains(at) ? 'ready' : 'loading',
          reason: contains(at) ? '' : 'Waiting for decoded video at the selected time'
        });
      } catch (error) {
        if (!dead && error.name !== 'AbortError') onStatus?.({
          state: 'unavailable',
          reason: error.message
        });
      } finally {
        busy = false;
        // Continue only after a successful append. The time window above bounds
        // prefetch; a gap or decoder error must not create a retry spin. This
        // also re-evaluates a seek made while the preceding read was pending.
        if (appendedSuccessfully && !dead) pump();
      }
    }
    async function update(next) {
      manifest = next;
      if (source) {
        pump();
        return;
      }
      const first = videoItems(next)[0];
      if (!first) {
        onStatus?.({
          state: 'unavailable',
          reason: 'No playable fragmented MP4 track supplied'
        });
        return;
      }
      const mime = first.metadata?.codec_mime || first.metadata?.mime_codec;
      if (!mime || !global.MediaSource?.isTypeSupported(mime)) {
        onStatus?.({
          state: 'unavailable',
          reason: 'Verified browser-compatible video codec not supplied'
        });
        return;
      }
      source = new MediaSource();
      url = URL.createObjectURL(source);
      video.src = url;
      // The browser may never open the track (unsupported pipeline, or a tab
      // that is not visible defers media loading). Say so instead of showing
      // "Opening video track…" forever.
      const watch = () => {
        openTimer = setTimeout(() => {
          openTimer = null;
          if (dead || buffer) return;
          if (global.document?.hidden) {
            onStatus?.({state: 'loading', reason: 'Video opens when this tab is visible'});
            watch();
          } else onStatus?.({state: 'unavailable', reason: 'Video track did not open within ' + OPEN_TIMEOUT_MS / 1000 + ' seconds. Recorded data is unchanged; reopen the session to retry.'});
        }, OPEN_TIMEOUT_MS);
      };
      watch();
      source.addEventListener('sourceopen', () => {
        clearTimeout(openTimer);
        openTimer = null;
        if (dead) return;
        try {
          buffer = source.addSourceBuffer(mime);
          pump();
        } catch (e) {
          onStatus?.({
            state: 'unavailable',
            reason: e.message
          });
        }
      }, {
        once: true
      });
    }
    const tick = () => pump();
    video.addEventListener('timeupdate', tick);
    video.addEventListener('seeking', tick);
    const timer = setInterval(tick, 1000);
    return {
      update,
      seek(t) {
        if (Number.isFinite(t)) {
          seekTarget = contains(t) ? null : t;
          video.currentTime = t;
          pump();
        }
      },
      dispose() {
        dead = true;
        abort.abort();
        clearInterval(timer);
        clearTimeout(openTimer);
        video.removeEventListener('timeupdate', tick);
        video.removeEventListener('seeking', tick);
        video.pause();
        video.removeAttribute('src');
        video.load();
        if (url) URL.revokeObjectURL(url);
      }
    };
  }
  function depthPixels(bytes, metadata, near, far, mode = 'depth') {
    const {
      width,
      height
    } = metadata || {};
    if (!Number.isSafeInteger(width) || !Number.isSafeInteger(height) || width < 1 || height < 1 || width * height > 2000000 || bytes.byteLength !== width * height * 2 || metadata.units !== 'millimetres' || metadata.alignment_frame !== 'rectified_left') throw Error('Depth payload or coordinate contract is invalid');
    const data = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength),
      rgba = new Uint8ClampedArray(width * height * 4);
    let invalid = 0;
    for (let i = 0; i < width * height; i++) {
      const mm = data.getUint16(i * 2, true),
        p = i * 4;
      if (mm === 0) {
        invalid++;
        const hatch = (i % width + Math.floor(i / width)) % 12 < 3;
        rgba[p] = hatch ? 110 : 15;
        rgba[p + 1] = hatch ? 78 : 15;
        rgba[p + 2] = hatch ? 18 : 15;
      } else if (mode === 'validity') {
        rgba[p] = 184;
        rgba[p + 1] = 216;
        rgba[p + 2] = 255;
      } else {
        const f = Math.max(0, Math.min(1, (mm / 1000 - near) / Math.max(.01, far - near)));
        rgba[p] = Math.round(240 * (1 - f));
        rgba[p + 1] = Math.round(245 * (1 - f) + 20 * f);
        rgba[p + 2] = Math.round(255 * (1 - f) + 42 * f);
      }
      rgba[p + 3] = 255;
    }
    return {
      rgba,
      width,
      height,
      invalid,
      sample(x, y) {
        if (x < 0 || y < 0 || x >= width || y >= height) return null;
        const value = data.getUint16((y * width + x) * 2, true);
        return value ? value / 1000 : null;
      }
    };
  }
  function sourcePlane(media, modality, atSeconds) {
    if (!Number.isFinite(atSeconds) || atSeconds < 0) return null;
    let selected = null, selectedTime = -1;
    for (const item of media?.items || []) {
      const meta = item.metadata || {};
      const time = meta.session_time_ms ?? meta.timeline_start_ms;
      const kind = meta.modality || meta.stream || (meta.format === 'depth_u16_le' ? 'depth' : null);
      if (kind !== modality || !Number.isFinite(time) || time < 0 || time > atSeconds * 1000
          || atSeconds * 1000 - time > 3000 || item.generation !== media.generation
          || (meta.clock_mapping_status !== 'verified' && meta.timeline_basis !== 'controller_receipt')) continue;
      if (time > selectedTime) { selected = item; selectedTime = time; }
    }
    return selected;
  }
  global.HomePerceptionLabMedia = {
    readFrame,
    transientFrameError,
    readObject,
    videoItems,
    createPlayer,
    depthPixels,
    sourcePlane
  };
})(window);
