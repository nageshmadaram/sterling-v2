/* Shared, session-only listening comfort. Never changes saved sound settings. */
(() => {
  function mount() {
    const reduced = matchMedia('(prefers-reduced-motion: reduce)');
    let deadline = 0, timer = null, returnFocus = null;
    const triggers = new Set();
    const announcement = document.createElement('div');
    announcement.className = 'media-announcement';
    announcement.setAttribute('role', 'status');
    const connection = document.createElement('div');
    connection.className = 'media-connection';
    connection.setAttribute('role', 'status');
    connection.innerHTML = '<span>You’re offline · Your saved channels are still here</span><button type="button" aria-label="Dismiss offline notice">✕</button>';
    connection.querySelector('button').addEventListener('click',()=>{connection.hidden=true;});
    let connectionDrag = null;
    connection.addEventListener('pointerdown',event=>{if(event.target.closest('button'))return;connectionDrag={id:event.pointerId,x:event.clientX,y:event.clientY};connection.setPointerCapture(event.pointerId);});
    connection.addEventListener('pointerup',event=>{
      if(connectionDrag?.id!==event.pointerId)return;
      if(Math.abs(event.clientX-connectionDrag.x)>64||connectionDrag.y-event.clientY>48)connection.hidden=true;
      connectionDrag=null;
    });
    connection.addEventListener('pointercancel',()=>{connectionDrag=null;});
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
    }
    connectionChanged();
    window.addEventListener('offline', connectionChanged);
    window.addEventListener('online', ()=>{connectionChanged();announce('Back online · Ready when you are');});
    function refresh() {
      if (deadline && Date.now() >= deadline) {
        deadline = 0;
        clearTimeout(timer); timer = null;
        // The Radio owner sets its explicit pause guard before pausing audio.
        window.dispatchEvent(new Event('sterling-media-sleep'));
        document.querySelectorAll('audio,video').forEach(media=>media.pause());
        announce('Sleep timer finished · Playback paused');
      }
      const minutes = deadline ? Math.max(1,Math.ceil((deadline-Date.now())/60000)) : 0;
      clearTimeout(timer); timer=null;
      if(deadline)timer=setTimeout(refresh,Math.max(1,(deadline-Date.now())-(minutes-1)*60000));
      for (const button of triggers) {
        if(!button.isConnected){triggers.delete(button);continue;}
        const label=minutes ? `◷ ${minutes}m` : '◷';if(button.textContent!==label)button.textContent=label;
        button.setAttribute('aria-label',minutes?`Sleep timer: ${minutes} minutes remaining`:'Set sleep timer');
        button.classList.toggle('media-timer-active',Boolean(deadline));
      }
      const status=dialog.querySelector('.media-timer-status');
      const label=minutes ? `Pausing in ${minutes} minute${minutes===1?'':'s'}` : 'No timer set';
      if(status.textContent!==label)status.textContent=label;
      dialog.querySelector('.media-timer-cancel').hidden = !deadline;
    }
    function close() { dialog.style.transform = ''; dialog.close(); if(returnFocus?.isConnected)returnFocus.focus({preventScroll:true}); }
    dialog.addEventListener('click',event=>{
      const choice = event.target.closest('[data-minutes]');
      if (choice) {
        deadline = Date.now()+Number(choice.dataset.minutes)*60000;
        clearTimeout(timer);
        refresh(); close(); announce(`Sleep timer set · ${choice.dataset.minutes} minutes`);
      } else if(event.target.closest('.media-timer-cancel')) {
        deadline = 0; clearTimeout(timer); timer = null;
        refresh(); close(); announce('Sleep timer cancelled');
      } else if(event.target.closest('[data-close]') || (event.target===dialog && (()=>{const r=dialog.getBoundingClientRect();return event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom;})())) close();
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
    attach(document.querySelector('#mobile-player-sheet .radio-np-actions>div'));
    attach(document.querySelector('.tm-now-footer>div'));
    // SW/AM is constructed lazily. Observe only direct body additions.
    const receiverObserver=new MutationObserver(()=>attach(document.querySelector('#sw-player-window header')));
    receiverObserver.observe(document.body,{childList:true});
    window.addEventListener('pagehide',event=>{
      if(event.persisted)return; // Preserve timers when the browser caches the page.
      receiverObserver.disconnect();clearTimeout(timer);clearTimeout(announcementTimeout);
    });
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
