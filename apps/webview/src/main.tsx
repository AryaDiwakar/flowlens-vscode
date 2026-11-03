import React from 'react';
import { createRoot } from 'react-dom/client';
import '@xyflow/react/dist/style.css';
import './style.css';
import './results.css';
import './empty-state.css';
import { App } from './ui/App';

createRoot(document.getElementById('root')!).render(<React.StrictMode><App /></React.StrictMode>);
