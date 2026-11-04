import { useEffect, useRef } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";
import { AnnouncerProvider } from "./Announcer";
import { ExternalLink, routeFocus } from "./PageParts";

export const REPO_URL = "https://github.com/lobojoel2003/readpath";

/** After each client-side navigation, move focus to the new page's h1. */
function useFocusOnRouteChange() {
  const { pathname } = useLocation();
  const lastPath = useRef(pathname);

  useEffect(() => {
    if (lastPath.current === pathname) return; // first load, or same page
    lastPath.current = pathname;
    routeFocus.until = Date.now() + 4000;
    window.scrollTo(0, 0);
    const heading = document.querySelector<HTMLElement>("main h1");
    const target = heading ?? document.getElementById("main");
    target?.focus({ preventScroll: true });
  }, [pathname]);
}

function Logo() {
  return (
    <svg className="logo__mark" width="32" height="32" viewBox="0 0 32 32" aria-hidden="true" focusable="false">
      <rect width="32" height="32" rx="8" className="logo__bg" />
      <path d="M10 7h8l5 5v13a1 1 0 0 1-1 1H10a1 1 0 0 1-1-1V8a1 1 0 0 1 1-1z" className="logo__page" />
      <path d="M18 7v5h5" className="logo__fold" />
      <path d="M12.5 19.5l2.5 2.5 5-5.5" className="logo__check" />
    </svg>
  );
}

export function Layout() {
  useFocusOnRouteChange();

  return (
    <>
      <a className="skip-link" href="#main">
        Skip to main content
      </a>
      <header className="site-header">
        <div className="container site-header__inner">
          <Link to="/" className="logo">
            <Logo />
            <span className="logo__text">Readpath</span>
          </Link>
          <nav aria-label="Main" className="site-nav">
            <ul>
              <li>
                <NavLink to="/" end>
                  Check PDFs
                </NavLink>
              </li>
              <li>
                <ExternalLink href={REPO_URL}>Source code</ExternalLink>
              </li>
            </ul>
          </nav>
        </div>
      </header>

      <main id="main" tabIndex={-1} className="site-main">
        <AnnouncerProvider>
          <div className="container">
            <Outlet />
          </div>
        </AnnouncerProvider>
      </main>

      <footer className="site-footer">
        <div className="container site-footer__inner">
          <p>
            <strong>Readpath</strong> is free and open source. The app is licensed AGPL-3.0; the checking
            core is Apache-2.0.
          </p>
          <p>
            <ExternalLink href={REPO_URL}>Readpath on GitHub</ExternalLink>
          </p>
          <p className="site-footer__fine">
            Readpath checks what software can check. A person should still review each document.
          </p>
        </div>
      </footer>
    </>
  );
}
