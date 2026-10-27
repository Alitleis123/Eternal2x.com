// The home page's download buttons open the newest release on GitHub. Point
// them at its zips instead. Releases before the rename to Upflow2x carry
// eternal2x-*.zip.
(function () {
    var links = document.querySelectorAll('a[data-asset]');
    if (!links.length || !window.fetch) return;
    fetch('https://api.github.com/repos/sidebandstudio/Upflow2x/releases/latest')
        .then(function (res) { return res.ok ? res.json() : null; })
        .then(function (release) {
            if (!release || !release.assets) return;
            links.forEach(function (link) {
                var name = link.getAttribute('data-asset');
                var names = [name, name.replace('upflow2x', 'eternal2x')];
                for (var n = 0; n < names.length; n++) {
                    for (var i = 0; i < release.assets.length; i++) {
                        if (release.assets[i].name === names[n]) {
                            link.href = release.assets[i].browser_download_url;
                            return;
                        }
                    }
                }
            });
        })
        .catch(function () {});
})();

// The demo on the home page: one video holds the original above the
// Upflow2x render, and a canvas shows the original left of a divider and
// the render right of it. One video keeps both sides on the same frame,
// where two videos drift apart.
(function () {
    var box = document.getElementById('compare');
    var video = document.getElementById('compare-video');
    var canvas = document.getElementById('compare-canvas');
    var slider = document.getElementById('compare-slider');
    var pause = document.getElementById('compare-pause');
    if (!box || !video || !canvas || !canvas.getContext) return;

    var ctx = canvas.getContext('2d');
    var W = canvas.width, H = canvas.height;
    var pos = 0.5;

    function draw() {
        if (video.readyState < 2) return;
        var x = Math.round(W * pos);
        // A zero-width source rectangle throws in some browsers.
        if (x > 0) ctx.drawImage(video, 0, 0, x, H, 0, 0, x, H);
        if (x < W) ctx.drawImage(video, x, H, W - x, H, x, 0, W - x, H);
        // A light line with an amber grip.
        ctx.fillStyle = '#f4f4f1';
        ctx.fillRect(x - 1.5, 0, 3, H);
        ctx.beginPath();
        ctx.arc(x, H / 2, 17, 0, Math.PI * 2);
        ctx.fillStyle = '#f2a93b';
        ctx.fill();
        ctx.lineWidth = 3;
        ctx.strokeStyle = '#101013';
        ctx.stroke();
        ctx.beginPath();
        ctx.moveTo(x - 4, H / 2 - 5); ctx.lineTo(x - 9, H / 2); ctx.lineTo(x - 4, H / 2 + 5);
        ctx.moveTo(x + 4, H / 2 - 5); ctx.lineTo(x + 9, H / 2); ctx.lineTo(x + 4, H / 2 + 5);
        ctx.lineWidth = 2.2;
        ctx.lineCap = 'round';
        ctx.lineJoin = 'round';
        ctx.stroke();
    }

    var tick = 0;
    function loop() {
        draw();
        tick = video.requestVideoFrameCallback ? video.requestVideoFrameCallback(loop)
                                              : requestAnimationFrame(loop);
    }
    function restart() {
        if (video.cancelVideoFrameCallback) video.cancelVideoFrameCallback(tick);
        else cancelAnimationFrame(tick);
        loop();
    }

    function setPos(value) {
        pos = Math.min(1, Math.max(0, value));
        slider.value = Math.round(pos * 100);
        draw();
    }

    function fromPointer(e) {
        var r = box.getBoundingClientRect();
        setPos((e.clientX - r.left) / r.width);
    }

    box.classList.add('js');
    video.controls = false;

    // A finger on the picture may be scrolling the page, so it only moves the
    // divider once it heads sideways, or lifts again without moving: a tap.
    // A mouse or pen moves it at once.
    var dragging = false, touch = null;
    box.addEventListener('pointerdown', function (e) {
        if (e.target === pause) return;
        if (e.pointerType === 'touch') {
            touch = { x: e.clientX, y: e.clientY };
            return;
        }
        dragging = true;
        box.setPointerCapture(e.pointerId);
        fromPointer(e);
    });
    box.addEventListener('pointermove', function (e) {
        if (touch && !dragging) {
            var dx = Math.abs(e.clientX - touch.x), dy = Math.abs(e.clientY - touch.y);
            if (dx > 8 && dx > dy) {
                dragging = true;
                box.setPointerCapture(e.pointerId);
            } else if (dy > 8) {
                touch = null;
            }
        }
        if (dragging) fromPointer(e);
    });
    box.addEventListener('pointerup', function (e) {
        if (touch && !dragging) fromPointer(e);
        dragging = false;
        touch = null;
    });
    box.addEventListener('pointercancel', function () {
        dragging = false;
        touch = null;
    });
    slider.addEventListener('input', function () { setPos(slider.value / 100); });
    box.addEventListener('click', function (e) {
        if (e.target !== pause) slider.focus({ preventScroll: true });
    });

    // The label follows what the video really does: a browser can refuse to
    // start it, and then it must still offer Play.
    function label() { pause.textContent = video.paused ? 'Play' : 'Pause'; }
    video.addEventListener('play', label);
    video.addEventListener('pause', label);
    function setPlaying(play) {
        if (play) video.play().catch(label);
        else video.pause();
    }
    pause.addEventListener('click', function () { setPlaying(video.paused); });

    // Speed and frame rate pick one of four renders of the same shots. The new
    // one picks up at the same moment, playing or paused as the last was.
    var controls = document.getElementById('compare-controls');
    if (controls) {
        var choice = { speed: 'half', rate: '24' };
        var pending = null;
        controls.hidden = false;
        controls.addEventListener('click', function (e) {
            var button = e.target.closest('button');
            if (!button) return;
            var key = button.dataset.speed ? 'speed' : 'rate';
            var value = button.dataset[key];
            if (choice[key] === value) return;
            choice[key] = value;
            controls.querySelectorAll('[data-' + key + ']').forEach(function (b) {
                b.setAttribute('aria-pressed', String(b === button));
            });
            // A second switch before the first render has loaded keeps the
            // first one's moment. By then the video reads as paused at 0.
            if (!pending) {
                pending = { playing: !video.paused,
                            at: video.duration ? video.currentTime / video.duration : 0 };
            }
            // The page's ?v= on the first video carries over, so a browser
            // holding older renders fetches these.
            var version = (video.getAttribute('src').split('?')[1] || '');
            video.src = 'assets/demo/compare-' + choice.rate + '-' + choice.speed + '.mp4'
                + (version ? '?' + version : '');
        });
        video.addEventListener('loadedmetadata', function () {
            if (!pending) return;
            var resume = pending;
            pending = null;
            video.currentTime = resume.at * video.duration;
            restart();
            if (resume.playing) setPlaying(true);
            else label();
        });
    }

    video.addEventListener('loadeddata', draw);
    video.addEventListener('seeked', draw);
    loop();
    // Motion only when the reader has not asked for less of it.
    var still = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    label();
    setPlaying(!still);

    // The first time the demo is on screen, the divider shows off both sides
    // once, in about three seconds: over to the right to show the original,
    // all the way left to show Upflow2x, then back to the middle. Touching the
    // demo first skips that, and any press or key during it puts the divider
    // straight back.
    if (still || !window.IntersectionObserver) return;
    var figure = box.closest('figure') || box;
    var sweeping = false, done = false, frame = 0, began = 0, from = pos, path = [];
    function stop() {
        if (done) return;
        done = true;
        seen.disconnect();
        cancelAnimationFrame(frame);
        window.removeEventListener('pointerdown', input, true);
        window.removeEventListener('keydown', input, true);
        if (sweeping) setPos(from);
        sweeping = false;
    }
    function input(e) {
        if (sweeping || figure.contains(e.target)) stop();
    }
    // Eases from each [seconds, divider] stop in path to the next.
    function step(now) {
        if (!began) began = now;
        var s = (now - began) / 1000, i = 1;
        while (i < path.length - 1 && s >= path[i][0]) i++;
        var a = path[i - 1], b = path[i];
        var t = Math.min(1, (s - a[0]) / (b[0] - a[0]));
        setPos(a[1] + (b[1] - a[1]) * (1 - Math.cos(Math.PI * t)) / 2);
        if (s < b[0]) frame = requestAnimationFrame(step);
        else stop();
    }
    function sweep() {
        if (done) return;
        // Nothing shows until the video has a frame to draw.
        if (video.readyState < 2) {
            video.addEventListener('loadeddata', sweep, { once: true });
            return;
        }
        from = pos;
        // 5% and 95% keep the grip whole on screen.
        path = [[0, from], [.7, .95], [.9, .95], [2.1, .05], [2.3, .05], [3, from]];
        sweeping = true;
        frame = requestAnimationFrame(step);
    }
    window.addEventListener('pointerdown', input, true);
    window.addEventListener('keydown', input, true);
    // Assistive tech can set the range with no press or key. Its value stays.
    slider.addEventListener('input', function () { sweeping = false; stop(); });
    // Asking for less motion later stops it too, and puts the divider back.
    var less = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)');
    if (less && less.addEventListener) {
        less.addEventListener('change', function (e) { if (e.matches) stop(); });
    }
    var seen = new IntersectionObserver(function (entries) {
        if (!entries[0].isIntersecting) return;
        seen.disconnect();
        // As the hero finishes coming in.
        setTimeout(sweep, 400);
    }, { threshold: .6 });
    seen.observe(box);
})();

// Sections below the hero rise in the first time they scroll into view, and
// the node chain in How it works runs once when most of it is in view. They
// stay as they are after that.
(function () {
    var items = document.querySelectorAll('[data-reveal]');
    var flow = document.querySelector('.flow');
    if (!window.IntersectionObserver || (!items.length && !flow)) return;
    function once(els, options) {
        var seen = new IntersectionObserver(function (entries) {
            entries.forEach(function (entry) {
                if (!entry.isIntersecting) return;
                entry.target.classList.add('in');
                seen.unobserve(entry.target);
            });
        }, options);
        els.forEach(function (el) { seen.observe(el); });
    }
    document.documentElement.classList.add('js-reveal');
    once(items, { rootMargin: '0px 0px -10% 0px' });
    if (flow) once([flow], { threshold: .5 });
})();

// Copy buttons on the help page's command blocks.
(function () {
    var blocks = document.querySelectorAll('.code');
    if (!blocks.length) return;
    // Tells a screen reader what the button did, which its label alone doesn't.
    var status = document.createElement('p');
    status.className = 'sr-only';
    status.setAttribute('role', 'status');
    document.body.appendChild(status);
    function say(text) {
        status.textContent = '';
        setTimeout(function () { status.textContent = text; }, 30);
    }
    blocks.forEach(function (block) {
        var code = block.querySelector('code');
        if (!code) return;
        var button = document.createElement('button');
        button.type = 'button';
        button.className = 'copy';
        button.textContent = 'Copy';
        button.setAttribute('aria-label', 'Copy this command');
        // Where the clipboard is out of reach, the command is selected
        // instead, ready for Ctrl+C.
        function select() {
            var range = document.createRange();
            range.selectNodeContents(code);
            var sel = window.getSelection();
            sel.removeAllRanges();
            sel.addRange(range);
            button.textContent = 'Selected';
            say('Command selected. Press Control C or Command C to copy it.');
            setTimeout(function () { button.textContent = 'Copy'; }, 2500);
        }
        button.addEventListener('click', function () {
            if (!navigator.clipboard) return select();
            navigator.clipboard.writeText(code.textContent.trim()).then(function () {
                button.textContent = 'Copied';
                say('Command copied.');
                setTimeout(function () { button.textContent = 'Copy'; }, 1500);
            }, select);
        });
        block.appendChild(button);
    });
})();

// The film starts on a frame inside Resolve under one big play button, so
// nobody mistakes it for a picture. Pressing it plays with sound.
(function () {
    document.querySelectorAll('.player').forEach(function (player) {
        var video = player.querySelector('video');
        var play = player.querySelector('.play');
        if (!video || !play) return;
        player.classList.add('js');
        video.controls = false;
        function started() {
            player.classList.add('playing');
            video.controls = true;
        }
        play.addEventListener('click', function () {
            started();
            // A refused play leaves the controls up for a second try.
            video.play().catch(function () {});
            video.focus();
        });
        video.addEventListener('play', started);
    });
})();

// The contact card posts to FormSubmit, which emails the message to the
// address in the form's action, hello@sideband.studio. Without script the
// form still posts there, and FormSubmit shows its own thank-you page.
(function () {
    var form = document.getElementById('contact-form');
    if (!form || !window.fetch || !window.FormData) return;
    var bug = document.getElementById('contact-bug');
    var messageLabel = document.getElementById('contact-message-label');
    var error = document.getElementById('contact-error');
    var sent = document.getElementById('contact-sent');
    var button = form.querySelector('button[type="submit"]');
    var LABELS = {
        'Bug report': 'What did you press, what happened, and what did you expect?',
        'Question': 'Your question',
        'Idea': 'Your idea'
    };

    // Only a bug report asks about Resolve and the computer. A disabled
    // fieldset leaves its answers out of the message.
    function topic() { return form.elements.topic.value; }
    function show() {
        var isBug = topic() === 'Bug report';
        bug.hidden = !isBug;
        bug.disabled = !isBug;
        messageLabel.textContent = LABELS[topic()] || 'Message';
    }
    form.addEventListener('change', function (e) {
        if (e.target.name === 'topic') show();
    });
    show();

    // Every field is locked while the message sends, so nothing typed in
    // that time is lost to the reset afterwards. Locking each control rather
    // than the bug fieldset leaves that fieldset's own setting alone.
    function lock(busy) {
        Array.prototype.forEach.call(form.elements, function (el) {
            if (el.tagName !== 'FIELDSET') el.disabled = busy;
        });
        button.textContent = busy ? 'Sending...' : 'Send message';
    }

    form.addEventListener('submit', function (e) {
        e.preventDefault();
        // Read before locking, because disabled fields are left out.
        var body = {};
        new FormData(form).forEach(function (value, key) { body[key] = value; });
        body._subject = 'Upflow2x site: ' + body.topic;
        var replyTo = body.email;
        error.hidden = true;
        lock(true);
        fetch(form.action.replace('formsubmit.co/', 'formsubmit.co/ajax/'), {
            method: 'POST',
            body: JSON.stringify(body),
            headers: { 'Content-Type': 'application/json', Accept: 'application/json' }
        }).then(function (res) {
            return res.json();
        }).then(function (answer) {
            // FormSubmit can answer without delivering, for example before
            // the form is activated, so its success flag decides.
            if (String(answer.success) !== 'true') throw new Error(answer.message);
            lock(false);
            document.getElementById('contact-sent-to').textContent = replyTo;
            form.reset();
            show();
            form.hidden = true;
            sent.hidden = false;
            sent.querySelector('h2').focus();
        }).catch(function () {
            lock(false);
            error.hidden = false;
            button.focus();
        });
    });

    document.getElementById('contact-again').addEventListener('click', function () {
        sent.hidden = true;
        form.hidden = false;
        form.querySelector('input[name="topic"]:checked').focus();
    });
})();
