'use strict';
// Only cache static presentation assets; never cache private APIs or the login screen.
const CACHE='monitor-static-v1';
const ASSETS=['/static/style.css','/static/app.js','/static/mark.svg','/static/manifest.webmanifest'];
self.addEventListener('install',e=>e.waitUntil(caches.open(CACHE).then(c=>c.addAll(ASSETS)).then(()=>self.skipWaiting())));
self.addEventListener('activate',e=>e.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(k=>k!==CACHE).map(k=>caches.delete(k)))).then(()=>self.clients.claim())));
self.addEventListener('fetch',e=>{
 const u=new URL(e.request.url);
 if(e.request.method!=='GET'||u.origin!==self.location.origin||!u.pathname.startsWith('/static/'))return;
 e.respondWith(fetch(e.request).then(res=>{
   if(res.ok){const copy=res.clone();caches.open(CACHE).then(c=>c.put(e.request,copy));}
   return res;
 }).catch(()=>caches.match(e.request)));
});
