/* Shared playback shortcuts. Live broadcasts expose only the operations they support. */
(()=>{
 const typing=target=>target?.closest?.('input,textarea,select,[contenteditable=true],dialog[open],[role=dialog]');
 function run(key,shift=false){
  const media=document.querySelector('#video-player,#audio-engine');if(!media)return false;
  const tv=media.tagName==='VIDEO',seek=seconds=>{
   if(Number.isFinite(media.duration)&&media.duration>0)media.currentTime=Math.max(0,Math.min(media.duration,media.currentTime+seconds));
   else if(media.seekable.length){const i=media.seekable.length-1;media.currentTime=Math.max(media.seekable.start(i),Math.min(media.seekable.end(i),media.currentTime+seconds));}
  };
  if(key==='k'||key===' '){if(typeof togglePlayPause==='function')togglePlayPause();else if(media.paused){void media.play().catch(()=>{});}else media.pause();return true;}
  if(key==='f'){if(tv&&typeof toggleFullscreenPlayer==='function'){void toggleFullscreenPlayer();return true;}const surface=tv?document.getElementById('video-surface-card'):document.documentElement;if(document.fullscreenElement)void document.exitFullscreen?.();else void surface.requestFullscreen?.().catch(()=>{});return true;}
  if(key==='m'){if(tv&&typeof toggleMuteAudio==='function')toggleMuteAudio();else if(typeof toggleMute==='function')toggleMute();else media.muted=!media.muted;return true;}
  if((key==='n'&&shift)||(key==='p'&&shift)){const next=key==='n'?window.playNextChannel:window.playPrevChannel;next?.();return true;}
  if(['arrowleft','arrowright','j','l'].includes(key)){seek((key==='j'||key==='arrowleft'?-1:1)*(key==='j'||key==='l'?10:5));return true;}
  if(key==='arrowup'||key==='arrowdown'){const volume=Math.max(0,Math.min(1,media.volume+(key==='arrowup' ? 0.05 : -0.05)));if(!tv&&typeof setAudioVolume==='function')setAudioVolume(volume);else media.volume=volume;return true;}
  if(/^\d$/.test(key)||key==='home'||key==='end'){if(Number.isFinite(media.duration))media.currentTime=media.duration*(key==='home'?0:key==='end'?1:Number(key)/10);return true;}
  if(key==='<'||key==='>'){if(Number.isFinite(media.duration))media.playbackRate=Math.max(.25,Math.min(2,media.playbackRate+(key==='>' ? 0.25 : -0.25)));return true;}
  if((key===','||key==='.')&&media.paused){seek(key===','?-1/30:1/30);return true;}
  if(key==='c'&&tv&&typeof toggleClosedCaptions==='function'){toggleClosedCaptions();return true;}
  if(key==='t'&&tv&&typeof toggleTheatreMode==='function'){toggleTheatreMode();return true;}
  if(key==='i'&&tv){if(typeof requestNativeBrowserPip==='function')void requestNativeBrowserPip();return true;}
  if(key==='/'){parent.postMessage({type:tv?'sterling.tv.focus-search':'sterling.radio.focus-search'},'*');return true;}
  return false;
 }
 document.addEventListener('keydown',event=>{if(event.defaultPrevented||event.ctrlKey||event.metaKey||event.altKey||typing(event.target))return;const key=event.key.toLowerCase();if(key===' '&&event.target.closest?.('button,[role=button],a'))return;if(run(key,event.shiftKey)){event.preventDefault();event.stopImmediatePropagation();}},true);
 addEventListener('message',event=>{if(event.source!==parent||!['sterling.tv.command','sterling.radio.command'].includes(event.data?.type)||event.data.action!=='shortcut')return;const value=event.data.value;if(typeof value==='string'&&value.length<=20)run(value.replace(/^shift\+/,''),value.startsWith('shift+'));});
})();
