/* Shared, session-only listening comfort. Never changes saved sound settings. */
(() => {
  function mount() {
    const reduced = matchMedia('(prefers-reduced-motion: reduce)');
    let deadline = 0, interval = null, returnFocus = null;
    const triggers = new Set();
    const announcement = document.createElement('div');
    announcement.className = 'media-announcement';
    announcement.setAttribute('role', 'status');
    const connection = document.createElement('div');
    connection.className = 'media-connection';
    connection.setAttribute('role', 'status');
    const dialog = document.createElement('dialog');
    dialog.className = 'media-comfort';
    dialog.setAttribute('aria-labelledby', 'media-comfort-title');
    dialog.innerHTML = `<div class="media-comfort-handle" aria-hidden="true"></div><header><div><small>MAKE YOURSELF COMFORTABLE</small><h2 id="media-comfort-title">Wind down</h2></div><button type="button" data-close aria-label="Close sleep timer">✕</button></header><p>Keep listening or watching. We’ll pause playback when your timer ends.</p><div class="media-timer-options">${[15,30,45,60,90].map(n=>`<button type="button" data-minutes="${n}">${n}<small>minutes</small></button>`).join('')}</div><p class="media-timer-status" role="status"></p><button type="button" class="media-timer-cancel">Cancel timer</button><small class="media-comfort-note">For this session only. Keep this tab open.</small>`;
    document.body.append(connection, announcement, dialog);
    let announcementTimeout;
    function announce(text) {
      announcement.textContent = text;
      announcement.classList.add('media-announcement-visible');
      clearTimeout(announcementTimeout);
      announcementTimeout = setTimeout(()=>announcement.classList.remove('media-announcement-visible'),4000);
    }
    function connectionChanged() {
      connection.hidden = navigator.onLine !== false;
      connection.textContent = 'You’re offline · Your saved channels are still here';
    }
    connectionChanged();
    window.addEventListener('offline', connectionChanged);
    window.addEventListener('online', ()=>{connectionChanged();announce('Back online · Ready when you are');});
    function refresh() {
      if (deadline && Date.now() >= deadline) {
        deadline = 0;
        clearInterval(interval); interval = null;
        // The Radio owner sets its explicit pause guard before pausing audio.
        window.dispatchEvent(new Event('sterling-media-sleep'));
        document.querySelectorAll('audio,video').forEach(media=>media.pause());
        announce('Sleep timer finished · Playback paused');
      }
      const minutes = deadline ? Math.max(1,Math.ceil((deadline-Date.now())/60000)) : 0;
      for (const button of triggers) {
        button.textContent = minutes ? `◷ ${minutes}m` : '◷';
        button.setAttribute('aria-label',minutes?`Sleep timer: ${minutes} minutes remaining`:'Set sleep timer');
        button.classList.toggle('media-timer-active',Boolean(deadline));
      }
      dialog.querySelector('.media-timer-status').textContent = minutes ? `Pausing in ${minutes} minute${minutes===1?'':'s'}` : 'No timer set';
      dialog.querySelector('.media-timer-cancel').hidden = !deadline;
    }
    function close() { dialog.style.transform = ''; dialog.close(); if(returnFocus?.isConnected)returnFocus.focus({preventScroll:true}); }
    dialog.addEventListener('click',event=>{
      const choice = event.target.closest('[data-minutes]');
      if (choice) {
        deadline = Date.now()+Number(choice.dataset.minutes)*60000;
        clearInterval(interval); interval = setInterval(refresh,1000);
        refresh(); close(); announce(`Sleep timer set · ${choice.dataset.minutes} minutes`);
      } else if(event.target.closest('.media-timer-cancel')) {
        deadline = 0; clearInterval(interval); interval = null;
        refresh(); close(); announce('Sleep timer cancelled');
      } else if(event.target.closest('[data-close]') || event.target===dialog) close();
    });
    dialog.addEventListener('keydown',event=>event.stopPropagation());
    dialog.addEventListener('cancel',event=>{event.preventDefault();close();});
    let drag = null;
    dialog.addEventListener('pointerdown',event=>{
      if(event.pointerType==='mouse' || event.target.closest('button') || !event.target.closest('header,.media-comfort-handle'))return;
      drag={id:event.pointerId,y:event.clientY,distance:0};
      dialog.setPointerCapture(event.pointerId);
    });
    dialog.addEventListener('pointermove',event=>{
      if(!drag || event.pointerId!==drag.id)return;
      drag.distance=Math.max(0,event.clientY-drag.y);
      if(!reduced.matches)dialog.style.transform=`translateY(${Math.min(180,drag.distance)}px)`;
    });
    function endDrag(event) {
      if(!drag || event.pointerId!==drag.id)return;
      const dismiss=event.type==='pointerup' && drag.distance>85;
      drag=null;dialog.style.transform='';
      if(dismiss)close();
    }
    dialog.addEventListener('pointerup',endDrag);
    dialog.addEventListener('pointercancel',endDrag);
    dialog.addEventListener('lostpointercapture',endDrag);
    function attach(container) {
      if(!container || container.querySelector('.media-timer-trigger'))return;
      const button=document.createElement('button');button.type='button';button.className='media-timer-trigger';button.title='Sleep timer';
      triggers.add(button);button.addEventListener('click',()=>{returnFocus=button;refresh();dialog.showModal();});
      container.append(button);refresh();
    }
    attach(document.querySelector('#mobile-player-sheet>div:first-child>div'));
    attach(document.querySelector('.tm-now-footer>div'));
    // SW/AM is constructed lazily. Observe only direct body additions.
    new MutationObserver(()=>attach(document.querySelector('#sw-player-window header'))).observe(document.body,{childList:true});
    document.addEventListener('visibilitychange',refresh);
    window.addEventListener('pageshow',refresh);
    document.addEventListener('pointerdown',event=>{
      if(reduced.matches || event.pointerType==='mouse')return;
      const button=event.target.closest('button');
      if(!button || button.disabled || !button.animate)return;
      button.animate([{scale:'1'},{scale:'.95'},{scale:'1'}],{duration:180,easing:'ease-out'});
    },{passive:true});
    refresh();
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',mount,{once:true});else mount();
})();
