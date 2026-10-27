const paths:Record<string,string>={
  dashboard:'M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z',
  astro:'M12 3v3 M12 18v3 M3 12h3 M18 12h3 M5.6 5.6l2 2 M16.4 16.4l2 2 M5.6 18.4l2-2 M16.4 7.6l2-2 M16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0',
  pcr:'M4 19V9 M10 19V4 M16 19v-7 M22 19V6',
  orders:'M6 3h12v18H6z M9 8h6 M9 12h6 M9 16h4',
  holdings:'M3 8l9-5 9 5 M5 10v9 M10 10v9 M15 10v9 M20 10v9 M3 21h18',
  positions:'M3 8h18v12H3z M8 8V4h8v4 M3 13h18 M10 13v3h4v-3',
  journal:'M5 3h14v18H5z M8 7h8 M8 11h8 M8 15h5',
  more:'M4 12h2 M11 12h2 M18 12h2',
  data:'M4 6c0-4 16-4 16 0s-16 4-16 0 M4 6v12c0 4 16 4 16 0V6 M4 12c0 4 16 4 16 0',
  backtest:'M4 4v6h6 M4 10a8 8 0 1 1 0 6 M12 7v5l3 2',
  connect:'M4 6h16 M4 12h16 M4 18h16 M8 3v6 M16 9v6 M10 15v6',
  help:'M9 9a3 3 0 1 1 5 2c-2 1-2 2-2 3 M12 18h.01 M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0',
};
export function NavigationIcon({id,size=18}:{id:string;size?:number}){return <svg aria-hidden="true" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d={paths[id]||paths.more}/></svg>;}
