import { create } from 'zustand';
interface InstallEvent extends Event {
  prompt:()=>Promise<void>;
  userChoice:Promise<{outcome:'accepted'|'dismissed'}>;
}
export const useAppInstall = create<{prompt:InstallEvent|null; installed:boolean}>(()=>({prompt:null,installed:false}));
export function initializeAppInstall() {
  if (typeof window === 'undefined') return;
  useAppInstall.setState({installed:window.matchMedia?.('(display-mode: standalone)').matches === true || (navigator as Navigator & {standalone?:boolean}).standalone === true});
  window.addEventListener('beforeinstallprompt',event=>{
    event.preventDefault();useAppInstall.setState({prompt:event as InstallEvent});
  });
  window.addEventListener('appinstalled',()=>useAppInstall.setState({installed:true,prompt:null}));
}
