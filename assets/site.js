(function () {
    const intervalMs = 2600;

    function activateSlide(slides, dots, index) {
        slides.forEach((slide, slideIndex) => {
            slide.classList.toggle("active", slideIndex === index);
        });
        dots.forEach((dot, dotIndex) => {
            const active = dotIndex === index;
            dot.classList.toggle("active", active);
            dot.setAttribute("aria-selected", String(active));
        });
    }

    function startCarousel(carousel) {
        const slides = Array.from(carousel.querySelectorAll(".screenshot-slide"));
        if (slides.length <= 1) {
            return;
        }

        const dotsWrap = document.createElement("div");
        dotsWrap.className = "carousel-dots";
        dotsWrap.setAttribute("role", "tablist");
        dotsWrap.setAttribute("aria-label", "Choose screenshot");

        const dots = slides.map((slide, slideIndex) => {
            const label = slide.querySelector("strong")?.textContent || `Screenshot ${slideIndex + 1}`;
            const dot = document.createElement("button");
            dot.className = "carousel-dot";
            dot.type = "button";
            dot.setAttribute("role", "tab");
            dot.setAttribute("aria-label", label);
            dot.addEventListener("click", () => {
                index = slideIndex;
                activateSlide(slides, dots, index);
            });
            dotsWrap.appendChild(dot);
            return dot;
        });
        carousel.appendChild(dotsWrap);

        let index = Math.max(0, slides.findIndex((slide) => slide.classList.contains("active")));
        let timer = null;
        activateSlide(slides, dots, index);

        const advance = () => {
            index = (index + 1) % slides.length;
            activateSlide(slides, dots, index);
        };

        const play = () => {
            if (!timer) {
                timer = window.setInterval(advance, intervalMs);
            }
        };

        const pause = () => {
            if (timer) {
                window.clearInterval(timer);
                timer = null;
            }
        };

        carousel.addEventListener("mouseenter", pause);
        carousel.addEventListener("mouseleave", play);
        carousel.addEventListener("focusin", pause);
        carousel.addEventListener("focusout", play);
        play();
    }

    document.addEventListener("DOMContentLoaded", () => {
        document.querySelectorAll(".screenshot-carousel").forEach(startCarousel);
    });
})();
