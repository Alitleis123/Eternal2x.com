// The phone menu: a drawer that slides over the page. Closed, it is hidden
// from the keyboard too (see .sidebar in style.css), so Tab cannot land on a
// link nobody can see.
(function () {
    var sidebar = document.getElementById('sidebar');
    var overlay = document.getElementById('overlay');
    var button = document.getElementById('menu-button');
    if (!sidebar || !overlay || !button) return;

    function setOpen(open, returnFocus) {
        sidebar.classList.toggle('open', open);
        overlay.classList.toggle('open', open);
        button.setAttribute('aria-expanded', open ? 'true' : 'false');
        if (open) {
            var first = sidebar.querySelector('a');
            if (first) first.focus();
        } else if (returnFocus) {
            button.focus();
        }
    }

    button.addEventListener('click', function () {
        setOpen(!sidebar.classList.contains('open'), false);
    });
    overlay.addEventListener('click', function () { setOpen(false, true); });
    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape' && sidebar.classList.contains('open')) setOpen(false, true);
    });
    // On a phone the drawer covers the page, so close it once a link is chosen.
    sidebar.querySelectorAll('a').forEach(function (link) {
        link.addEventListener('click', function () { setOpen(false, false); });
    });
})();

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
        ctx.fillStyle = 'rgba(255, 255, 255, 0.9)';
        ctx.fillRect(x - 1, 0, 2, H);
        ctx.beginPath();
        ctx.arc(x, H / 2, 14, 0, Math.PI * 2);
        ctx.fill();
        ctx.fillStyle = '#0b0b0f';
        ctx.font = 'bold 13px sans-serif';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText('↔', x, H / 2 + 1);
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

    box.addEventListener('pointerdown', function (e) {
        if (e.target === pause) return;
        box.setPointerCapture(e.pointerId);
        fromPointer(e);
    });
    box.addEventListener('pointermove', function (e) {
        if (box.hasPointerCapture(e.pointerId)) fromPointer(e);
    });
    slider.addEventListener('input', function () { setPos(slider.value / 100); });
    box.addEventListener('click', function (e) {
        if (e.target !== pause) slider.focus({ preventScroll: true });
    });

    function setPlaying(play) {
        if (play) video.play().catch(function () {});
        else video.pause();
        pause.textContent = play ? 'Pause' : 'Play';
    }
    pause.addEventListener('click', function () { setPlaying(video.paused); });

    video.addEventListener('loadeddata', draw);
    video.addEventListener('seeked', draw);
    loop();
    // Motion only when the reader has not asked for less of it.
    var still = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    setPlaying(!still);
})();
