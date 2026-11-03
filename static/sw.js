/* F9: the student page opens on bad classroom Wi-Fi. Scope /s/ only. Pages: network first, cached copy when offline.
   Static files: cache first. Requests to the Worker (another origin) are never touched. */
'use strict';
var CACHE = 'daybook-shell-v1';
var STATIC = ['/static/app.css', '/static/app.js', '/static/icon-192.png'];

self.addEventListener('install', function (e) {
  e.waitUntil(caches.open(CACHE).then(function (c) { return c.addAll(STATIC); }).then(function () { return self.skipWaiting(); }));
});
self.addEventListener('activate', function (e) {
  e.waitUntil(caches.keys().then(function (keys) {
    return Promise.all(keys.filter(function (k) { return k !== CACHE; }).map(function (k) { return caches.delete(k); }));
  }).then(function () { return self.clients.claim(); }));
});
self.addEventListener('fetch', function (e) {
  var req = e.request, url = new URL(req.url);
  if (req.method !== 'GET' || url.origin !== self.location.origin) return;
  if (url.pathname.indexOf('/static/') === 0) {
    e.respondWith(caches.match(req).then(function (hit) {
      return hit || fetch(req).then(function (res) {
        if (res.ok) { var copy = res.clone(); caches.open(CACHE).then(function (c) { c.put(req, copy); }); }
        return res;
      });
    }));
    return;
  }
  if (url.pathname.indexOf('/s/') === 0) {
    e.respondWith(fetch(req).then(function (res) {
      if (res.ok) { var copy = res.clone(); caches.open(CACHE).then(function (c) { c.put(req, copy); }); }
      return res;
    }).catch(function () {
      return caches.match(req, { ignoreSearch: true }).then(function (hit) { return hit || caches.match(url.pathname, { ignoreSearch: true }); });
    }));
  }
});

/* F15.3: opt-in Web Push. The payload is generic (no names, no scores); a tap opens the subscriber's own page. */
self.addEventListener('push', function (e) {
  var d = {};
  try { d = e.data ? e.data.json() : {}; } catch (x) { d = {}; }
  e.waitUntil(self.registration.showNotification(d.title || 'AMP 학습 기록', {
    body: d.body || '', icon: '/static/icon-192.png', badge: '/static/icon-192.png', data: { url: d.url || '/' }, tag: 'daybook'
  }));
});
self.addEventListener('notificationclick', function (e) {
  e.notification.close();
  var url = (e.notification.data && e.notification.data.url) || '/';
  if (new URL(url, self.location.origin).origin !== self.location.origin) url = '/';      /* only our own pages */
  e.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function (list) {
    for (var i = 0; i < list.length; i++) if (list[i].url === url && 'focus' in list[i]) return list[i].focus();
    return self.clients.openWindow(url);
  }));
});
