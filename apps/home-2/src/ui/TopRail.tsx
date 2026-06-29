import {
  Activity,
  Camera,
  Menu,
  Settings,
  SlidersHorizontal,
  Sun,
  UserRound
} from "lucide-react";
import type { ServiceState } from "../lib/home-core/types";

type Props = {
  trackerState: ServiceState;
  daylight: boolean;
  onToggleDaylight: () => void;
  onOpenSettings: () => void;
  onOpenSurface: (surface: string) => void;
};

export function TopRail({ trackerState, daylight, onToggleDaylight, onOpenSettings, onOpenSurface }: Props) {
  return (
    <header className="top-rail">
      <div className="brand-lockup">
        <div className="brand-title">home</div>
        <div className={`live-pill is-${trackerState}`}>
          <span />
          tracker {trackerState === "sim" ? "sim" : trackerState}
        </div>
      </div>
      <nav className="top-actions" aria-label="Home 2 navigation">
        <button className="icon-button" type="button" aria-label="Intelligence Atlas" title="Intelligence Atlas" onClick={() => onOpenSurface("atlas")}>
          <Activity size={18} />
        </button>
        <button className="icon-button" type="button" aria-label="People" title="People" onClick={() => onOpenSurface("people")}>
          <UserRound size={18} />
        </button>
        <button className="icon-button" type="button" aria-label="Cameras" title="Cameras" onClick={() => onOpenSurface("cameras")}>
          <Camera size={18} />
        </button>
        <button className={`icon-button ${daylight ? "is-active" : ""}`} type="button" aria-label="Daylight" title="Daylight" onClick={onToggleDaylight}>
          <Sun size={18} />
        </button>
        <button className="icon-button" type="button" aria-label="Controls" title="Controls" onClick={() => onOpenSurface("lights")}>
          <SlidersHorizontal size={18} />
        </button>
        <button className="icon-button" type="button" aria-label="Settings" title="Settings" onClick={onOpenSettings}>
          <Settings size={18} />
        </button>
        <button className="icon-button" type="button" aria-label="Help" title="Help" onClick={() => onOpenSurface("help")}>
          <Menu size={19} />
        </button>
      </nav>
    </header>
  );
}
