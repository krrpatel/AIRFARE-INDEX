export const metadata = {
  title: "India Airfare Price Index",
  description: "Airfare price index dashboard",
};

import "./styles.css";

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>
        <header className="topbar">
          <div className="topbar-inner">
            <div className="ministry">MoSPI research dashboard</div>
            <div className="site-title">Airfare Price Index Dashboard</div>
          </div>
        </header>
        <nav className="nav">
          <div className="nav-inner">
            <a href="/">Overview</a>
            <a href="/routes">Routes</a>
            <a href="/airlines">Airlines</a>
            <a href="/map">India Map</a>
            <a href="/analytics">Analytics</a>
            <a href="/settings">Settings</a>
          </div>
        </nav>
        {children}
      </body>
    </html>
  );
}
