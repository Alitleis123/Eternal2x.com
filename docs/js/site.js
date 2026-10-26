// The demo on the home page: one video holds the original above the
// Eternal2x render, and a canvas shows the original left of a divider and
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
        // The divider is a strip of paper with a red pencil grip.
        ctx.fillStyle = '#f2eee4';
        ctx.fillRect(x - 1.5, 0, 3, H);
        ctx.beginPath();
        ctx.arc(x, H / 2, 17, 0, Math.PI * 2);
        ctx.fillStyle = '#d6361f';
        ctx.fill();
        ctx.lineWidth = 3;
        ctx.strokeStyle = '#f2eee4';
        ctx.stroke();
        ctx.beginPath();
        ctx.moveTo(x - 4, H / 2 - 5); ctx.lineTo(x - 9, H / 2); ctx.lineTo(x - 4, H / 2 + 5);
        ctx.moveTo(x + 4, H / 2 - 5); ctx.lineTo(x + 9, H / 2); ctx.lineTo(x + 4, H / 2 + 5);
        ctx.lineWidth = 2.2;
        ctx.lineCap = 'round';
        ctx.lineJoin = 'round';
        ctx.stroke();
    }

    function loop() {
        draw();
        if (video.requestVideoFrameCallback) video.requestVideoFrameCallback(loop);
        else requestAnimationFrame(loop);
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

    video.addEventListener('loadeddata', draw);
    video.addEventListener('seeked', draw);
    loop();
    // Motion only when the reader has not asked for less of it.
    var still = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    label();
    setPlaying(!still);
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
