import React, { useEffect, useMemo, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './styles.css';

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8787';

async function api(path, options) {
  const response = await fetch(`${API_BASE}${path}`, options);
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `HTTP ${response.status}`);
  }
  return response.json();
}

function Pill({ children, tone = 'default' }) {
  return <span className={`pill pill-${tone}`}>{children}</span>;
}

function formatJson(value) {
  return JSON.stringify(value, null, 2);
}

function formatDateTime(value) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
}

function stripTags(value) {
  return String(value || '').replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim();
}

function UploadBox({ onImported }) {
  const [isDragging, setDragging] = useState(false);
  const [status, setStatus] = useState('');
  const isNative = Boolean(window.resumeIntelNative?.isElectron);

  async function uploadFile(file) {
    setStatus(`Импортирую ${file.name}...`);
    const data = new FormData();
    data.append('file', file);
    const event = await api('/api/import', { method: 'POST', body: data });
    setStatus(`Готово: ${event.company_name || event.subject || file.name}`);
    onImported(event);
  }

  async function handleFiles(files) {
    const fileList = Array.from(files || []);
    if (fileList.length === 0) return;
    try {
      for (const file of fileList) {
        await uploadFile(file);
      }
      if (fileList.length > 1) {
        setStatus(`Импортировано файлов: ${fileList.length}`);
      }
    } catch (error) {
      setStatus(`Ошибка: ${error.message}`);
    }
  }

  async function importNativeMailSelection() {
    if (!window.resumeIntelNative?.readSelectedMailMessages) {
      setStatus('Drop не содержит файла. Для прямого drag из Mail нужен Electron-режим.');
      return;
    }

    setStatus('Пытаюсь прочитать выбранное письмо из Apple Mail...');
    try {
      const result = await window.resumeIntelNative.readSelectedMailMessages();
      const messages = result?.messages || [];
      if (messages.length === 0) {
        setStatus('Mail не вернул выбранные письма. Выберите письмо в Mail и перетащите его снова.');
        return;
      }

      let latestEvent = null;
      for (const message of messages) {
        latestEvent = await api('/api/import/native-mail', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(message),
        });
        onImported(latestEvent);
      }

      setStatus(`Импортировано из Mail: ${messages.length}. ${latestEvent?.company_name || latestEvent?.subject || ''}`);
    } catch (error) {
      setStatus(`Не удалось прочитать Mail: ${error.message}`);
    }
  }

  async function handleDrop(event) {
    event.preventDefault();
    setDragging(false);

    const files = event.dataTransfer.files;
    if (files?.length > 0) {
      await handleFiles(files);
      return;
    }

    const html = event.dataTransfer.getData('text/html');
    const plain = event.dataTransfer.getData('text/plain');
    const textLooksLikeOnlyMailSubject =
      !html &&
      plain &&
      plain.trim().length < 120 &&
      /резюме привлекло внимание|подходящие вакансии/i.test(plain);

    if (textLooksLikeOnlyMailSubject && isNative) {
      await importNativeMailSelection();
      return;
    }

    if (textLooksLikeOnlyMailSubject && !isNative) {
      setStatus('Mail отдал браузеру только тему письма, без тела. Для прямого drag из Mail откройте Electron-режим и повторите импорт.');
      return;
    }

    if (html || plain) {
      const blob = new Blob([html || plain], { type: html ? 'text/html' : 'text/plain' });
      const file = new File([blob], html ? 'dropped-mail.html' : 'dropped-mail.txt', {
        type: html ? 'text/html' : 'text/plain',
      });
      await handleFiles([file]);
      return;
    }

    await importNativeMailSelection();
  }

  return (
    <section
      className={`upload ${isDragging ? 'upload-active' : ''}`}
      onDragOver={(event) => {
        event.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(event) => {
        handleDrop(event);
      }}
    >
      <div>
        <h2>Импорт письма</h2>
        <p>Перетащите сюда письмо из Mail или файл `.eml`, `.txt`, `.html`.</p>
        <label className="button">
          Выбрать файл
          <input
            type="file"
            multiple
            accept=".eml,.txt,.html,.htm,message/rfc822,text/plain,text/html"
            onChange={(event) => handleFiles(event.target.files)}
          />
        </label>
        {isNative && (
          <button className="secondary" type="button" onClick={importNativeMailSelection}>
            Импортировать выбранное из Mail
          </button>
        )}
      </div>
      <p className="muted">
        {status || (isNative
          ? 'Electron-режим: если Mail не отдаст файл, приложение попробует прочитать выбранное письмо напрямую.'
          : 'Web-режим: принимает файлы. Для прямого drag из Mail запустите Electron-режим.')}
      </p>
    </section>
  );
}

function EventList({ events, selectedId, onSelect }) {
  return (
    <section className="panel event-list">
      <div className="panel-header">
        <h2>События внимания</h2>
        <Pill>{events.length}</Pill>
      </div>
      {events.length === 0 ? (
        <p className="muted">Пока нет импортированных писем.</p>
      ) : (
        events.map((event) => (
          <button
            className={`event-card ${selectedId === event.id ? 'event-card-active' : ''}`}
            key={event.id}
            onClick={() => onSelect(event.id)}
          >
            <div className="event-title">{event.company_name || 'Компания не определена'}</div>
            <div className="event-subtitle">{event.subject || event.raw_filename}</div>
            <div className="event-meta">
              <Pill tone={event.source === 'hh' ? 'green' : 'default'}>{event.source}</Pill>
              <Pill tone={event.event_type === 'resume_attention' ? 'blue' : 'default'}>{event.event_type}</Pill>
            </div>
          </button>
        ))
      )}
    </section>
  );
}

function MatchCard({ match }) {
  return (
    <div className="match-card">
      <div className="match-row">
        <strong>{match.title}</strong>
        <Pill tone={match.score > 0.12 ? 'green' : 'default'}>{Math.round(match.score * 100)}%</Pill>
      </div>
      {match.overlap_terms?.length > 0 && (
        <p className="terms">{match.overlap_terms.join(', ')}</p>
      )}
      {match.recommendations?.length > 0 && (
        <ul>
          {match.recommendations.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

function VacancyForm({ company, onSaved }) {
  const [title, setTitle] = useState('');
  const [url, setUrl] = useState('');
  const [description, setDescription] = useState('');
  const [status, setStatus] = useState('');

  async function submit(event) {
    event.preventDefault();
    if (!company || !title.trim() || !description.trim()) {
      setStatus('Нужны компания, название и текст вакансии.');
      return;
    }
    try {
      await api('/api/vacancies', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ company, title, url: url || null, description }),
      });
      setTitle('');
      setUrl('');
      setDescription('');
      setStatus('Вакансия добавлена, matching обновлен.');
      onSaved();
    } catch (error) {
      setStatus(`Ошибка: ${error.message}`);
    }
  }

  return (
    <form className="vacancy-form" onSubmit={submit}>
      <h3>Вакансия компании</h3>
      <p className="muted">Вставьте текст релевантной вакансии компании, чтобы уточнить пересечение с CV-типами.</p>
      <input value={title} onChange={(event) => setTitle(event.target.value)} placeholder="Название вакансии" />
      <input value={url} onChange={(event) => setUrl(event.target.value)} placeholder="URL, если есть" />
      <textarea
        value={description}
        onChange={(event) => setDescription(event.target.value)}
        placeholder="Текст вакансии / требования / обязанности"
        rows={6}
      />
      <div className="form-row">
        <button type="submit">Добавить вакансию</button>
        <span className="muted">{status}</span>
      </div>
    </form>
  );
}

function VacancyMatchSummary({ vacancy }) {
  const recommended = vacancy.recommended_profile;
  const bestCv = vacancy.cv_type_matches?.[0];
  const bestResume = vacancy.hh_resume_matches?.[0];
  return (
    <div className="terms">
      {recommended?.resume_title ? (
        <p>Дайджест для резюме: {recommended.resume_title}</p>
      ) : null}
      <p>
        {bestCv ? `CV: ${bestCv.title} · ${Math.round(bestCv.score * 100)}%${bestCv.recommended ? ' · из дайджеста' : ''}` : 'CV match пока не рассчитан'}
        {bestResume ? ` · Резюме: ${bestResume.title} · ${Math.round(bestResume.score * 100)}%${bestResume.recommended ? ' · из дайджеста' : ''}` : ''}
      </p>
      {recommended?.cv_type_title && !bestCv?.recommended ? (
        <p className="muted">Типовой CV из письма: {recommended.cv_type_title}</p>
      ) : null}
      {recommended?.hh_resume_title && !bestResume?.recommended ? (
        <p className="muted">Локальное резюме из письма: {recommended.hh_resume_title}</p>
      ) : null}
    </div>
  );
}

function VacancyCard({ vacancy, onSave, savingId, onOpenDetail, detailId, onOpenEmployer, employerLoadingId }) {
  return (
    <div className="match-card vacancy-card">
      <div className="match-row">
        <div>
          <strong>{vacancy.title}</strong>
          <p className="terms">
            {vacancy.company_name || vacancy.employer_name || 'Компания не определена'}
            {vacancy.area_name ? ` · ${vacancy.area_name}` : ''}
            {vacancy.salary ? ` · ${vacancy.salary}` : ''}
          </p>
        </div>
        {onSave || onOpenDetail ? (
          <div className="button-row">
            {onOpenDetail && (
              <button type="button" className="secondary compact" onClick={() => onOpenDetail(vacancy.external_id)} disabled={detailId === vacancy.external_id}>
                {detailId === vacancy.external_id ? 'Загружаю...' : 'Карточка'}
              </button>
            )}
            {onOpenEmployer && vacancy.employer_id && (
              <button type="button" className="secondary compact" onClick={() => onOpenEmployer(vacancy.employer_id)} disabled={employerLoadingId === vacancy.employer_id}>
                {employerLoadingId === vacancy.employer_id ? 'Загружаю...' : 'Работодатель'}
              </button>
            )}
            {onSave && (
              <button type="button" className="secondary compact" onClick={() => onSave(vacancy.external_id)} disabled={savingId === vacancy.external_id}>
                {savingId === vacancy.external_id ? 'Сохраняю...' : 'Сохранить'}
              </button>
            )}
          </div>
        ) : (
          <Pill tone={vacancy.source === 'hh_api' ? 'green' : 'default'}>{vacancy.source || 'manual'}</Pill>
        )}
      </div>
      {vacancy.url && (
        <p className="terms">
          <a href={vacancy.url} target="_blank" rel="noreferrer">Открыть на HH</a>
        </p>
      )}
      <p className="muted">{(vacancy.description || '').slice(0, 700)}</p>
      <VacancyMatchSummary vacancy={vacancy} />
    </div>
  );
}

function HhVacancySearch({ company, resumeTitle, onSaved }) {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState([]);
  const [detail, setDetail] = useState(null);
  const [employer, setEmployer] = useState(null);
  const [status, setStatus] = useState('');
  const [savingId, setSavingId] = useState('');
  const [detailId, setDetailId] = useState('');
  const [employerLoadingId, setEmployerLoadingId] = useState('');

  async function searchVacancies(event) {
    event.preventDefault();
    const text = query.trim() || resumeTitle || '';
    if (!company && !text) {
      setStatus('Нужна компания или текст поиска.');
      return;
    }
    setStatus('Ищу вакансии HH...');
    try {
      const params = new URLSearchParams();
      if (company) params.set('company', company);
      if (text) params.set('text', text);
      params.set('per_page', '10');
      const result = await api(`/api/channels/hh/vacancies/search?${params.toString()}`);
      setResults(result.items || []);
      setDetail(null);
      setEmployer(null);
      setStatus(`Найдено: ${result.found || 0}. Показано: ${(result.items || []).length}.`);
    } catch (error) {
      setStatus(`Ошибка поиска HH: ${error.message}`);
    }
  }

  async function saveVacancy(vacancyId) {
    if (!vacancyId) return;
    setSavingId(vacancyId);
    setStatus('Сохраняю вакансию локально...');
    try {
      await api(`/api/channels/hh/vacancies/${encodeURIComponent(vacancyId)}/save`, { method: 'POST' });
      setStatus('Вакансия сохранена локально, matching обновлен.');
      onSaved();
    } catch (error) {
      setStatus(`Ошибка сохранения: ${error.message}`);
    } finally {
      setSavingId('');
    }
  }

  async function openVacancyDetail(vacancyId) {
    if (!vacancyId) return;
    setDetailId(vacancyId);
    setStatus('Загружаю карточку вакансии...');
    try {
      const result = await api(`/api/channels/hh/vacancies/${encodeURIComponent(vacancyId)}`);
      setDetail(result);
      setStatus('Карточка вакансии загружена.');
    } catch (error) {
      setStatus(`Ошибка карточки HH: ${error.message}`);
    } finally {
      setDetailId('');
    }
  }

  async function openEmployerDetail(employerId) {
    if (!employerId) return;
    setEmployerLoadingId(employerId);
    setStatus('Загружаю работодателя...');
    try {
      const result = await api(`/api/channels/hh/employers/${encodeURIComponent(employerId)}`);
      setEmployer(result);
      setStatus('Карточка работодателя загружена.');
    } catch (error) {
      setStatus(`Ошибка работодателя HH: ${error.message}`);
    } finally {
      setEmployerLoadingId('');
    }
  }

  return (
    <section className="vacancy-form">
      <h3>Поиск вакансий HH</h3>
      <p className="muted">Ищем доступным HH API по компании и тексту. Сохраненные вакансии используются для локального matching.</p>
      <form onSubmit={searchVacancies}>
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder={resumeTitle ? `Текст поиска, например: ${resumeTitle}` : 'Текст поиска / роль / ключевые слова'}
        />
        <div className="form-row">
          <button type="submit">Найти в HH</button>
          <span className="muted">{status}</span>
        </div>
      </form>
      {results.length > 0 && (
        <div className="matches">
          {results.map((vacancy) => (
            <VacancyCard
              key={vacancy.external_id || vacancy.url}
              vacancy={vacancy}
              onSave={saveVacancy}
              savingId={savingId}
              onOpenDetail={openVacancyDetail}
              detailId={detailId}
              onOpenEmployer={openEmployerDetail}
              employerLoadingId={employerLoadingId}
            />
          ))}
        </div>
      )}
      {detail && (
        <>
          <h3>Карточка вакансии HH</h3>
          <VacancyCard vacancy={detail} onSave={saveVacancy} savingId={savingId} />
        </>
      )}
      {employer && (
        <div className="match-card vacancy-card">
          <div className="match-row">
            <strong>{employer.name || 'Работодатель HH'}</strong>
            <Pill>{employer.open_vacancies ? `${employer.open_vacancies} вакансий` : 'hh employer'}</Pill>
          </div>
          {employer.alternate_url && (
            <p className="terms">
              <a href={employer.alternate_url} target="_blank" rel="noreferrer">Открыть работодателя на HH</a>
            </p>
          )}
          <p className="muted">{stripTags(employer.description || '').slice(0, 900)}</p>
        </div>
      )}
    </section>
  );
}

function ResumeMatchCard({ match }) {
  return (
    <div className="match-card">
      <div className="match-row">
        <strong>{match.title}</strong>
        <Pill tone={match.score > 0.5 ? 'green' : 'default'}>{Math.round(match.score * 100)}%</Pill>
      </div>
      <p className="terms">{match.status}{match.notes ? ` · ${match.notes}` : ''}</p>
      {match.overlap_terms?.length > 0 && <p className="terms">{match.overlap_terms.join(', ')}</p>}
    </div>
  );
}

function Detail({ event, onChanged }) {
  if (!event) {
    return (
      <section className="panel detail empty">
        <h2>Выберите событие</h2>
        <p className="muted">После импорта письма здесь появится разбор: компания, резюме, совпадения с CV-типами и идеи усиления.</p>
      </section>
    );
  }

  return (
    <section className="panel detail">
      <div className="panel-header">
        <h2>{event.company_name || 'Компания не определена'}</h2>
        <Pill tone="blue">confidence {Math.round(event.confidence * 100)}%</Pill>
      </div>

      <div className="grid">
        <div>
          <div className="label">Источник</div>
          <div>{event.source}</div>
        </div>
        <div>
          <div className="label">Тип письма</div>
          <div>{event.event_type}</div>
        </div>
        <div>
          <div className="label">Резюме</div>
          <div>{event.resume_title || 'Не определено из письма'}</div>
        </div>
        <div>
          <div className="label">Файл</div>
          <div>{event.raw_filename}</div>
        </div>
      </div>

      <h3>Опубликованное HH-резюме</h3>
      {event.hh_resume_matches?.length > 0 ? (
        <div className="matches">
          {event.hh_resume_matches.map((match) => (
            <ResumeMatchCard key={match.id} match={match} />
          ))}
        </div>
      ) : (
        <p className="muted">Справочник текущих HH-резюме пока пуст. Заполните `apps/resume-intel/config/hh_resumes.json` фактическими названиями резюме из HH.</p>
      )}

      <h3>Проектные CV-типы</h3>
      <div className="matches">
        {event.cv_type_matches?.map((match) => (
          <MatchCard key={match.slug} match={match} />
        ))}
      </div>

      <h3>Сохраненные вакансии компании</h3>
      {event.related_vacancies?.length > 0 ? (
        <div className="matches">
          {event.related_vacancies.map((vacancy) => (
            <VacancyCard key={vacancy.id} vacancy={vacancy} />
          ))}
        </div>
      ) : (
        <p className="muted">Пока нет сохраненных вакансий. Найдите через HH API или добавьте текст вручную.</p>
      )}

      <HhVacancySearch company={event.company_name} resumeTitle={event.resume_title} onSaved={onChanged} />
      <VacancyForm company={event.company_name} onSaved={onChanged} />

      <h3>Тема письма</h3>
      <p>{event.subject || 'Без темы'}</p>

      <h3>Фрагмент текста</h3>
      <pre>{(event.raw_text || '').slice(0, 1800)}</pre>
    </section>
  );
}

function CvTypes({ cvTypes, selectedId, onSelect }) {
  return (
    <section className="panel cv-types">
      <h2>CV-типы</h2>
      {cvTypes.map((item) => (
        <button
          className={`cv-type list-button ${selectedId === item.slug ? 'list-button-active' : ''}`}
          key={item.slug}
          type="button"
          onClick={() => onSelect(item.slug)}
        >
          <strong>{item.title}</strong>
          <div className="muted">
            {item.slug}
            {item.updated_at ? ` · обновлено ${formatDateTime(item.updated_at)}` : ''}
          </div>
        </button>
      ))}
    </section>
  );
}

function HhResumes({ resumes, selectedId, onSelect }) {
  return (
    <section className="panel cv-types">
      <h2>Опубликованные резюме</h2>
      {resumes.length === 0 ? (
        <p className="muted">Справочник пуст. Импортируйте PDF/RTF/HTML/TXT из HH, LinkedIn или другого канала.</p>
      ) : (
        resumes.map((item) => (
          <button
            className={`cv-type list-button ${selectedId === item.id ? 'list-button-active' : ''}`}
            key={item.id}
            type="button"
            onClick={() => onSelect(item.id)}
          >
            <strong>{item.title}</strong>
            <div className="muted">
              {item.channel || 'hh'} · {item.status}
              {item.source ? ` · ${item.source}` : ''}
              {item.external_id ? ` · external ID ${item.external_id}` : ''}
              {item.source_filename ? ` · ${item.source_filename}` : ''}
              {item.api_updated_at ? ` · HH ${item.api_updated_at}` : ''}
              {item.updated_at ? ` · обновлено ${formatDateTime(item.updated_at)}` : ''}
            </div>
            {item.keywords?.length > 0 && <p className="terms">{item.keywords.slice(0, 8).join(', ')}</p>}
          </button>
        ))
      )}
    </section>
  );
}

function HhApiSyncBox({ status, onSynced }) {
  const [syncStatus, setSyncStatus] = useState('');
  const connected = Boolean(status?.connected);
  const tokenSaved = Boolean(status?.token_saved);
  const applicantApiSupported = Boolean(status?.applicant_api_supported);

  async function syncResumes() {
    if (!applicantApiSupported) {
      setSyncStatus('Синхронизация HH-резюме через API недоступна: HH закрыл соискательский API. Используйте импорт из файла и локальное редактирование.');
      return;
    }
    setSyncStatus('Синхронизирую актуальные резюме из HH API...');
    try {
      const result = await api('/api/channels/hh/sync-resumes', { method: 'POST' });
      const errorNote = result.errors?.length ? ` Ошибок деталей: ${result.errors.length}.` : '';
      setSyncStatus(`Синхронизировано резюме: ${result.synced} из ${result.found}.${errorNote}`);
      onSynced();
    } catch (error) {
      setSyncStatus(`Ошибка HH API sync: ${error.message}`);
    }
  }

  return (
    <section className="panel cv-types">
      <div className="panel-header">
        <h2>HH: резюме и вакансии</h2>
        <Pill tone="default">локальный режим</Pill>
      </div>
      <p className="muted">
        HH подтвердил, что соискательский API закрыт: резюме и отклики от лица соискателя через API больше недоступны.
        Основной путь для резюме — импорт из файла, локальное редактирование и события из писем HH.
      </p>
      <p className="muted">
        Через API остаются сценарии поиска и просмотра вакансий с токеном приложения; это отдельный будущий контур без синхронизации резюме.
      </p>
      {connected && !tokenSaved && (
        <p className="muted">
          HH-профиль подключён старым OAuth-flow. Для текущего локального режима это не критично: резюме ведём внутри Resume Intel.
        </p>
      )}
      {!connected && <p className="muted">Подключение HH больше не требуется для локального ведения резюме.</p>}
      {syncStatus && <p className="muted">{syncStatus}</p>}
      <button type="button" onClick={syncResumes} disabled={!applicantApiSupported || !connected || !tokenSaved}>
        Синхронизация резюме через HH API недоступна
      </button>
    </section>
  );
}

function DetailMeta({ label, value }) {
  if (!value) return null;
  return (
    <div>
      <div className="label">{label}</div>
      <div>{value}</div>
    </div>
  );
}

function StructuredText({ value }) {
  const paragraphs = String(value || '')
    .split(/\n{2,}/)
    .map((paragraph) => paragraph.trim())
    .filter(Boolean);
  if (paragraphs.length === 0) return null;
  return (
    <div className="structured-text">
      {paragraphs.map((paragraph, index) => (
        <p key={`${paragraph}-${index}`}>{paragraph}</p>
      ))}
    </div>
  );
}

function TextBlock({ title, value }) {
  if (!value || (Array.isArray(value) && value.length === 0)) return null;
  return (
    <div className="structure-block">
      <h3>{title}</h3>
      {Array.isArray(value) ? (
        <ul>
          {value.map((item) => (
            <li key={typeof item === 'string' ? item : JSON.stringify(item)}>{typeof item === 'string' ? item : JSON.stringify(item)}</li>
          ))}
        </ul>
      ) : (
        <StructuredText value={value} />
      )}
    </div>
  );
}

function isMarkdownDocument(value) {
  return Boolean(value && (/^#{1,3}\s+/m.test(value) || /\*\*[^*]+\*\*/.test(value)));
}

function renderInlineMarkdown(value) {
  const parts = String(value || '').split(/(\*\*[^*]+\*\*)/g);
  return parts.map((part, index) => {
    const bold = part.match(/^\*\*([^*]+)\*\*$/);
    if (bold) {
      return <strong key={`${part}-${index}`}>{bold[1]}</strong>;
    }
    return <React.Fragment key={`${part}-${index}`}>{part}</React.Fragment>;
  });
}

function parseMarkdownBlocks(content) {
  const blocks = [];
  let paragraph = [];
  let list = [];

  function flushParagraph() {
    if (paragraph.length > 0) {
      blocks.push({ type: 'paragraph', text: paragraph.join(' ') });
      paragraph = [];
    }
  }

  function flushList() {
    if (list.length > 0) {
      blocks.push({ type: 'list', items: list });
      list = [];
    }
  }

  for (const line of String(content || '').split('\n')) {
    const trimmed = line.trim();
    if (!trimmed) {
      flushParagraph();
      flushList();
      continue;
    }

    const heading = trimmed.match(/^(#{1,4})\s+(.+)$/);
    if (heading) {
      flushParagraph();
      flushList();
      blocks.push({ type: 'heading', level: heading[1].length, text: heading[2] });
      continue;
    }

    const bullet = trimmed.match(/^[-*•]\s+(.+)$/);
    if (bullet) {
      flushParagraph();
      list.push(bullet[1]);
      continue;
    }

    flushList();
    paragraph.push(trimmed);
  }

  flushParagraph();
  flushList();
  return blocks;
}

function MarkdownDocument({ content }) {
  const blocks = parseMarkdownBlocks(content);
  if (blocks.length === 0) {
    return <p className="muted">Документ пуст.</p>;
  }

  return (
    <div className="markdown-document">
      {blocks.map((block, index) => {
        if (block.type === 'heading') {
          const level = Math.min(Math.max(block.level, 1), 4);
          const Tag = `h${level}`;
          return <Tag key={`${block.text}-${index}`}>{renderInlineMarkdown(block.text)}</Tag>;
        }
        if (block.type === 'list') {
          return (
            <ul key={`list-${index}`}>
              {block.items.map((item, itemIndex) => (
                <li key={`${item}-${itemIndex}`}>{renderInlineMarkdown(item)}</li>
              ))}
            </ul>
          );
        }
        return <p key={`${block.text}-${index}`}>{renderInlineMarkdown(block.text)}</p>;
      })}
    </div>
  );
}

function EditableMarkdownDocument({ title, content, onSave, markdown = true }) {
  const [isEditing, setEditing] = useState(false);
  const [draft, setDraft] = useState(content || '');
  const [status, setStatus] = useState('');

  useEffect(() => {
    setDraft(content || '');
    setStatus('');
    setEditing(false);
  }, [content]);

  async function saveDraft() {
    setStatus('Сохраняю...');
    try {
      await onSave(draft);
      setStatus('Сохранено.');
      setEditing(false);
    } catch (error) {
      setStatus(`Ошибка сохранения: ${error.message}`);
    }
  }

  return (
    <div className="editable-document">
      <div className="panel-header">
        <h3>{title}</h3>
        <div className="button-row">
          {isEditing ? (
            <>
              <button type="button" onClick={saveDraft}>Сохранить</button>
              <button type="button" className="secondary" onClick={() => { setDraft(content || ''); setEditing(false); }}>
                Отмена
              </button>
            </>
          ) : (
            <button type="button" className="secondary" onClick={() => setEditing(true)}>Редактировать</button>
          )}
        </div>
      </div>
      {status && <p className="muted">{status}</p>}
      {isEditing ? (
        <textarea
          className="document-editor"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          spellCheck="false"
        />
      ) : markdown ? (
        <MarkdownDocument content={content || ''} />
      ) : (
        <pre className="document-viewer">{content || 'Текст резюме не сохранен.'}</pre>
      )}
    </div>
  );
}

function ResumeStructure({ structure }) {
  if (!structure) return null;

  return (
    <div className="structure-view">
      <div className="panel-header">
        <h2>Структура резюме</h2>
        <Pill>{structure.experience?.length || 0} мест работы</Pill>
      </div>
      <TextBlock title="Профиль / О себе" value={structure.summary} />
      {structure.skills?.length > 0 && (
        <div className="structure-block">
          <h3>Навыки</h3>
          <div className="keyword-cloud">
            {structure.skills.slice(0, 80).map((skill) => (
              <Pill key={skill}>{skill}</Pill>
            ))}
          </div>
        </div>
      )}
      {structure.experience?.length > 0 && (
        <div className="structure-block">
          <h3>Опыт работы</h3>
          <div className="timeline">
            {structure.experience.map((item, index) => (
              <div className="timeline-item" key={`${item.period}-${item.company}-${index}`}>
                <div className="label">{item.period}</div>
                <strong>{item.position || 'Позиция не определена'}</strong>
                <div className="muted">{item.company || 'Компания не определена'}</div>
                {item.achievements?.length > 0 ? (
                  <ul>
                    {item.achievements.map((achievement) => (
                      <li key={achievement}>{achievement}</li>
                    ))}
                  </ul>
                ) : (
                  <p>{item.description}</p>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
      <TextBlock title="Образование" value={structure.education} />
      <TextBlock title="Сертификации / обучение" value={structure.certifications} />
      <TextBlock title="Языки" value={structure.languages} />
      {structure.parser_notes?.length > 0 && <p className="muted">{structure.parser_notes.join(' ')}</p>}
    </div>
  );
}

function HhResumeDetail({ detail, onSaveContent }) {
  if (!detail) {
    return (
      <section className="panel detail empty">
        <h2>Выберите опубликованное резюме</h2>
        <p className="muted">Здесь будет полный текст импортированного резюме, метаданные, source-файл и keywords.</p>
      </section>
    );
  }

  const rawText = detail.raw_text || '';
  const markdown = isMarkdownDocument(rawText);

  return (
    <section className="panel detail">
      <div className="panel-header">
        <h2>{detail.title}</h2>
        <Pill tone="green">{detail.channel || 'hh'}</Pill>
      </div>
      <div className="grid">
        <DetailMeta label="Статус" value={detail.status} />
        <DetailMeta label="Источник" value={detail.source} />
        <DetailMeta label="External ID" value={detail.external_id} />
        <DetailMeta label="Файл" value={detail.source_filename} />
        <DetailMeta label="Обновлено" value={detail.updated_at} />
        <DetailMeta label="HH updated_at" value={detail.api_updated_at} />
        <DetailMeta label="Импортов" value={detail.import_count ? String(detail.import_count) : ''} />
        <DetailMeta label="URL" value={detail.url} />
        <DetailMeta label="Raw API data" value={detail.raw_api_data ? 'сохранено' : ''} />
      </div>
      {detail.notes && <p className="muted">{detail.notes}</p>}
      {detail.keywords?.length > 0 && (
        <>
          <h3>Keywords</h3>
          <div className="keyword-cloud">
            {detail.keywords.slice(0, 60).map((keyword) => (
              <Pill key={keyword}>{keyword}</Pill>
            ))}
          </div>
        </>
      )}
      {markdown ? (
        <EditableMarkdownDocument title="Документ резюме" content={rawText} onSave={onSaveContent} />
      ) : (
        <>
          <p className="muted">
            Legacy-импорт из HH HTML: редактируйте текст ниже. После сохранения markdown-версии (как у CTO/DWH) кнопка «Редактировать» останется наверху, структура обновится автоматически.
          </p>
          <EditableMarkdownDocument title="Содержимое резюме" content={rawText} onSave={onSaveContent} markdown={false} />
          <ResumeStructure structure={detail.parsed_structure} />
        </>
      )}
    </section>
  );
}

function CvTypeDetail({ detail, activeDocument, onDocumentSelect, onSaveDocument }) {
  if (!detail) {
    return (
      <section className="panel detail empty">
        <h2>Выберите CV-тип</h2>
        <p className="muted">Здесь можно будет посмотреть analysis, requirements, tailored CV, cover letter и interview prep.</p>
      </section>
    );
  }

  const documents = detail.documents || [];
  const selectedDocument =
    documents.find((doc) => doc.filename === activeDocument) ||
    documents.find((doc) => doc.filename === 'tailored_cv.md') ||
    documents[0];

  return (
    <section className="panel detail">
      <div className="panel-header">
        <h2>{detail.title}</h2>
        <Pill tone="blue">{detail.slug}</Pill>
      </div>
      {detail.keywords?.length > 0 && (
        <div className="keyword-cloud">
          {detail.keywords.slice(0, 40).map((keyword) => (
            <Pill key={keyword}>{keyword}</Pill>
          ))}
        </div>
      )}
      <div className="document-tabs">
        {documents.map((document) => (
          <button
            className={`secondary compact ${selectedDocument?.filename === document.filename ? 'tab-active' : ''}`}
            key={document.filename}
            type="button"
            onClick={() => onDocumentSelect(document.filename)}
          >
            {document.title}
          </button>
        ))}
      </div>
      <EditableMarkdownDocument
        title={selectedDocument?.title || 'Документ'}
        content={selectedDocument?.content || ''}
        onSave={(content) => onSaveDocument(selectedDocument?.filename, content)}
      />
    </section>
  );
}

function ResumeImportBox({ resumes, onImported }) {
  const [isDragging, setDragging] = useState(false);
  const [isExpanded, setExpanded] = useState(false);
  const [channel, setChannel] = useState('hh');
  const [importMode, setImportMode] = useState('new');
  const [targetResumeId, setTargetResumeId] = useState('');
  const [title, setTitle] = useState('');
  const [url, setUrl] = useState('');
  const [status, setStatus] = useState('');

  async function uploadResume(file) {
    setStatus(`Импортирую ${file.name}...`);
    const data = new FormData();
    data.append('file', file);
    data.append('channel', channel);
    data.append('title', title);
    data.append('url', url);
    data.append('import_mode', importMode);
    data.append('target_resume_id', targetResumeId);
    const result = await api('/api/hh-resumes/import', { method: 'POST', body: data });
    setStatus(`${result.updated_existing ? 'Обновлено' : 'Добавлено'} резюме: ${result.title}`);
    if (importMode === 'new') {
      setTitle('');
      setUrl('');
    }
    onImported(result);
  }

  async function handleFiles(files) {
    const fileList = Array.from(files || []);
    if (fileList.length === 0) return;
    try {
      for (const file of fileList) {
        await uploadResume(file);
      }
      if (fileList.length > 1) {
        setStatus(`Импортировано резюме: ${fileList.length}`);
      }
    } catch (error) {
      setStatus(`Ошибка: ${error.message}`);
    }
  }

  return (
    <section
      className={`panel resume-import ${isDragging && isExpanded ? 'upload-active' : ''}`}
      onDragOver={(event) => {
        if (!isExpanded) return;
        event.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(event) => {
        if (!isExpanded) return;
        event.preventDefault();
        setDragging(false);
        handleFiles(event.dataTransfer.files);
      }}
    >
      <div className="panel-header">
        <div>
          <h2>Импорт опубликованного резюме</h2>
          {!isExpanded && <p className="muted">PDF/RTF/HTML/TXT из HH, создание новой записи или обновление существующей.</p>}
        </div>
        <button className="secondary" type="button" onClick={() => setExpanded((value) => !value)}>
          {isExpanded ? 'Свернуть' : 'Импортировать резюме'}
        </button>
      </div>

      {isExpanded && (
        <>
          <p className="muted">
            Перетащите PDF/RTF/HTML/TXT из HH. Можно создать новое резюме или актуализировать уже существующую запись.
          </p>

      <div className="import-form-grid">
        <label>
          <span className="label">Канал</span>
          <select value={channel} onChange={(event) => setChannel(event.target.value)}>
            <option value="hh">HH</option>
            <option value="linkedin">LinkedIn</option>
            <option value="telegram">Telegram</option>
            <option value="manual">Ручной импорт</option>
          </select>
        </label>
        <label>
          <span className="label">Режим</span>
          <select value={importMode} onChange={(event) => setImportMode(event.target.value)}>
            <option value="new">Новое резюме</option>
            <option value="update">Обновить существующее</option>
          </select>
        </label>
        <label>
          <span className="label">Что обновить</span>
          <select
            disabled={importMode !== 'update' || resumes.length === 0}
            value={targetResumeId}
            onChange={(event) => setTargetResumeId(event.target.value)}
          >
            <option value="">Выберите резюме</option>
            {resumes.map((resume) => (
              <option key={resume.id} value={resume.id}>
                {resume.title}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span className="label">Название, если нужно переопределить</span>
          <input value={title} onChange={(event) => setTitle(event.target.value)} placeholder="Например: Директор по данным и AI / CDO" />
        </label>
        <label>
          <span className="label">URL резюме, если есть</span>
          <input value={url} onChange={(event) => setUrl(event.target.value)} placeholder="https://hh.ru/resume/..." />
        </label>
      </div>

      <div className="form-row">
        <label className="button">
          Выбрать файл резюме
          <input
            type="file"
            multiple
            accept=".pdf,.rtf,.txt,.html,.htm,application/pdf,text/rtf,application/rtf,text/plain,text/html"
            onChange={(event) => handleFiles(event.target.files)}
          />
        </label>
        <span className="muted">{status || 'Можно импортировать несколько файлов по очереди.'}</span>
      </div>
      <p className="muted">
        Если в URL или тексте файла есть HH resume id, приложение попробует автоматически обновить уже связанную запись.
        Если id не виден, используйте режим “Обновить существующее”.
      </p>
        </>
      )}
    </section>
  );
}

function Sidebar({ activePage, onNavigate, stats }) {
  const pages = [
    { id: 'overview', title: 'Обзор', hint: 'сводка' },
    { id: 'attention', title: 'События внимания', hint: `${stats.events} событий` },
    { id: 'workflow', title: 'Вакансии и отклики', hint: 'pipeline' },
    { id: 'resumes', title: 'Резюме', hint: `${stats.hhResumes} HH / ${stats.cvTypes} CV` },
    { id: 'channels', title: 'Каналы', hint: 'HH, TG, LinkedIn' },
  ];

  return (
    <aside className="sidebar">
      <div className="brand">
        <div className="brand-mark">RI</div>
        <div>
          <strong>Resume Intel</strong>
          <span>career signal hub</span>
        </div>
      </div>

      <nav className="nav">
        {pages.map((page) => (
          <button
            className={`nav-item ${activePage === page.id ? 'nav-item-active' : ''}`}
            key={page.id}
            type="button"
            onClick={() => onNavigate(page.id)}
          >
            <span>{page.title}</span>
            <small>{page.hint}</small>
          </button>
        ))}
      </nav>

      <div className="sidebar-card">
        <div className="label">Статус HH API</div>
        <strong>OAuth подключается</strong>
        <p className="muted">Заявка одобрена. Настройте redirect URI и подключите HH на вкладке “Каналы”.</p>
      </div>
    </aside>
  );
}

function TopBar({ activePage, onRefresh }) {
  const titles = {
    overview: 'Обзор сигналов',
    attention: 'События внимания',
    workflow: 'Вакансии и отклики',
    resumes: 'Резюме и CV-типы',
    channels: 'Каналы данных',
    'hh-diagnostics': 'Диагностика HH',
  };

  return (
    <header className="topbar">
      <div>
        <div className="eyebrow">Local intelligence dashboard</div>
        <h1>{titles[activePage] || 'Resume Intel'}</h1>
      </div>
      <button className="ghost" onClick={onRefresh}>Обновить</button>
    </header>
  );
}

function StatCard({ label, value, note }) {
  return (
    <div className="stat-card">
      <div className="label">{label}</div>
      <strong>{value}</strong>
      <span>{note}</span>
    </div>
  );
}

function OverviewPage({ events, cvTypes, hhResumes, onNavigate }) {
  const companies = new Set(events.map((event) => event.company_name).filter(Boolean));
  const latest = events.slice(0, 5);

  return (
    <div className="page-grid">
      <section className="hero-panel">
        <div>
          <div className="eyebrow">Multi-channel roadmap</div>
          <h2>Единый центр сигналов по рынку, резюме и вакансиям</h2>
          <p className="muted">
            Сейчас работает импорт из Apple Mail для HH-писем. Архитектура UI уже разделяет источники, чтобы позже добавить HH API,
            Telegram-каналы, LinkedIn и другие каналы без переделки рабочих сценариев.
          </p>
        </div>
        <button type="button" onClick={() => onNavigate('channels')}>Настроить каналы</button>
      </section>

      <div className="stats">
        <StatCard label="События" value={events.length} note="импортировано из писем/API" />
        <StatCard label="Компании" value={companies.size} note="проявили интерес" />
        <StatCard label="HH-резюме" value={hhResumes.length} note="после API sync" />
        <StatCard label="CV-типы" value={cvTypes.length} note="проектные профили" />
      </div>

      <section className="panel">
        <div className="panel-header">
          <h2>Последние сигналы</h2>
          <button className="secondary compact" type="button" onClick={() => onNavigate('attention')}>Открыть</button>
        </div>
        <div className="signal-list">
          {latest.length === 0 ? (
            <p className="muted">Пока нет импортированных событий.</p>
          ) : (
            latest.map((event) => (
              <div className="signal-row" key={event.id}>
                <div>
                  <strong>{event.company_name || 'Компания не определена'}</strong>
                  <span>{event.resume_title || event.subject}</span>
                </div>
                <Pill tone={event.source === 'hh' ? 'green' : 'default'}>{event.source}</Pill>
              </div>
            ))
          )}
        </div>
      </section>
    </div>
  );
}

function AttentionPage({ events, selected, setSelectedId, onImported, onChanged }) {
  return (
    <div className="page-grid">
      <UploadBox onImported={onImported} />
      <div className="content-layout">
        <EventList events={events} selectedId={selected?.id} onSelect={setSelectedId} />
        <Detail event={selected} onChanged={onChanged} />
      </div>
    </div>
  );
}

function VacancyMailImportBox({ onImported }) {
  const [status, setStatus] = useState('');
  const isNative = Boolean(window.resumeIntelNative?.isElectron);

  async function importSelectedVacancyMail() {
    if (!window.resumeIntelNative?.readSelectedMailMessages) {
      setStatus('Импорт из Apple Mail доступен только в Electron-окне приложения.');
      return;
    }
    setStatus('Читаю выбранное письмо из Apple Mail...');
    try {
      const result = await window.resumeIntelNative.readSelectedMailMessages();
      const messages = result?.messages || [];
      if (messages.length === 0) {
        setStatus('Mail не вернул выбранные письма. Выберите письмо с вакансиями в Apple Mail.');
        return;
      }
      let totalSaved = 0;
      let totalParsed = 0;
      let lastProfile = '';
      for (const message of messages) {
        const imported = await api('/api/vacancies/import/native-mail', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(message),
        });
        totalSaved += imported.saved?.length || 0;
        totalParsed += imported.parsed || 0;
        if (imported.recommended_profile?.resume_title) {
          lastProfile = imported.recommended_profile.resume_title;
        } else if (imported.resume_title) {
          lastProfile = imported.resume_title;
        }
      }
      const profileText = lastProfile ? ` Профиль из письма: ${lastProfile}.` : '';
      setStatus(`Импортировано писем: ${messages.length}. Разобрано вакансий: ${totalParsed}. Сохранено: ${totalSaved}.${profileText}`);
      onImported();
    } catch (error) {
      setStatus(`Не удалось импортировать вакансии из Mail: ${error.message}`);
    }
  }

  return (
    <section className="panel">
      <div className="panel-header">
        <div>
          <h2>Импорт вакансий из Mail</h2>
          <p className="muted">Для HH-дайджестов “Новые/подходящие вакансии” попробуем найти полные карточки через HH API. Для других рассылок сохраним то, что разобрали из письма.</p>
        </div>
        <Pill tone={isNative ? 'green' : 'default'}>{isNative ? 'Electron' : 'браузер'}</Pill>
      </div>
      <div className="form-row">
        <button type="button" onClick={importSelectedVacancyMail} disabled={!isNative}>
          Импортировать выбранное письмо из Mail
        </button>
        <span className="muted">{status}</span>
      </div>
    </section>
  );
}

function SavedVacanciesPanel({ vacancies }) {
  return (
    <section className="panel">
      <div className="panel-header">
        <h2>Сохраненные вакансии</h2>
        <Pill>{vacancies.length}</Pill>
      </div>
      {vacancies.length === 0 ? (
        <p className="muted">После импорта дайджеста или сохранения из HH API вакансии появятся здесь.</p>
      ) : (
        <div className="matches">
          {vacancies.slice(0, 12).map((vacancy) => (
            <VacancyCard key={vacancy.id} vacancy={vacancy} />
          ))}
        </div>
      )}
    </section>
  );
}

function WorkflowPage({ events, vacancies, selected, setSelectedId, onChanged }) {
  const companies = Array.from(new Set(events.map((event) => event.company_name).filter(Boolean)));

  return (
    <div className="page-grid">
      <VacancyMailImportBox onImported={onChanged} />
      <SavedVacanciesPanel vacancies={vacancies} />
      <div className="content-layout">
        <section className="panel event-list">
          <div className="panel-header">
            <h2>Pipeline компаний</h2>
            <Pill>{companies.length}</Pill>
          </div>
          {companies.length === 0 ? (
            <p className="muted">Компании появятся после импорта HH-писем или дайджестов вакансий из Mail.</p>
          ) : (
            companies.map((company) => {
              const event = events.find((item) => item.company_name === company);
              return (
                <button
                  className={`event-card ${selected?.id === event?.id ? 'event-card-active' : ''}`}
                  key={company}
                  type="button"
                  onClick={() => setSelectedId(event.id)}
                >
                  <div className="event-title">{company}</div>
                  <div className="event-subtitle">{event?.resume_title || 'Резюме не определено'}</div>
                  <div className="event-meta">
                    <Pill tone="blue">вакансии</Pill>
                    <Pill>отклики</Pill>
                  </div>
                </button>
              );
            })
          )}
        </section>

        <section className="panel detail">
          <div className="panel-header">
            <h2>{selected?.company_name || 'Выберите компанию'}</h2>
            <Pill>workflow</Pill>
          </div>
          <p className="muted">
            Здесь будет рабочий сценарий по компании: открытые вакансии, релевантные отклики, пересечение требований с резюме и следующие действия.
            Сейчас можно вручную добавить текст вакансии или импортировать дайджест вакансий из Mail.
          </p>
          {selected ? <VacancyForm company={selected.company_name} onSaved={onChanged} /> : null}
          {selected ? <Detail event={selected} onChanged={onChanged} /> : null}
        </section>
      </div>
    </div>
  );
}

function ResumesPage({ hhResumes, cvTypes, hhStatus, onImported }) {
  const [detailKind, setDetailKind] = useState('hh');
  const [selectedHhId, setSelectedHhId] = useState('');
  const [selectedCvSlug, setSelectedCvSlug] = useState('');
  const [hhDetail, setHhDetail] = useState(null);
  const [cvDetail, setCvDetail] = useState(null);
  const [activeCvDocument, setActiveCvDocument] = useState('');
  const [detailStatus, setDetailStatus] = useState('');
  const [operationsOpen, setOperationsOpen] = useState(false);

  async function selectHhResume(id) {
    setDetailKind('hh');
    setSelectedHhId(id);
    setDetailStatus('Загружаю HH-резюме...');
    try {
      const detail = await api(`/api/hh-resumes/${encodeURIComponent(id)}`);
      setHhDetail(detail);
      setDetailStatus('');
    } catch (error) {
      setDetailStatus(`Ошибка: ${error.message}`);
    }
  }

  async function selectCvType(slug) {
    setDetailKind('cv');
    setSelectedCvSlug(slug);
    setDetailStatus('Загружаю CV-тип...');
    try {
      const detail = await api(`/api/cv-types/${encodeURIComponent(slug)}`);
      setCvDetail(detail);
      setActiveCvDocument(
        detail.documents?.find((document) => document.filename === 'tailored_cv.md')?.filename ||
          detail.documents?.[0]?.filename ||
          '',
      );
      setDetailStatus('');
    } catch (error) {
      setDetailStatus(`Ошибка: ${error.message}`);
    }
  }

  async function saveHhResumeContent(content) {
    const detail = await api(`/api/hh-resumes/${encodeURIComponent(selectedHhId)}/content`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content }),
    });
    setHhDetail(detail);
    onImported(detail);
  }

  async function saveCvTypeDocument(filename, content) {
    if (!filename) throw new Error('Документ не выбран');
    const detail = await api(`/api/cv-types/${encodeURIComponent(selectedCvSlug)}/documents/${encodeURIComponent(filename)}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content }),
    });
    setCvDetail(detail);
    setActiveCvDocument(filename);
    onImported(detail);
  }

  useEffect(() => {
    if (selectedHhId || selectedCvSlug) return;
    if (hhResumes.length > 0) {
      selectHhResume(hhResumes[0].id);
      return;
    }
    if (cvTypes.length > 0) {
      selectCvType(cvTypes[0].slug);
    }
  }, [hhResumes, cvTypes, selectedHhId, selectedCvSlug]);

  return (
    <div className="page-grid">
      <section className="panel resume-workbench-header">
        <div>
          <div className="eyebrow">CV workspace</div>
          <h2>Резюме и CV-типы</h2>
          <p className="muted">Основной сценарий здесь — выбрать резюме или CV-тип и смотреть детальную карточку.</p>
        </div>
        <button className="secondary" type="button" onClick={() => setOperationsOpen((value) => !value)}>
          {operationsOpen ? 'Скрыть операции' : 'Операции с резюме'}
        </button>
      </section>

      {operationsOpen && (
        <div className="operations-panel">
          <HhApiSyncBox status={hhStatus} onSynced={onImported} />
          <ResumeImportBox resumes={hhResumes} onImported={onImported} />
        </div>
      )}

      <div className="content-layout resumes-layout">
        <div>
          <HhResumes resumes={hhResumes} selectedId={selectedHhId} onSelect={selectHhResume} />
          <CvTypes cvTypes={cvTypes} selectedId={selectedCvSlug} onSelect={selectCvType} />
        </div>
        <div>
          {detailStatus && <p className="muted">{detailStatus}</p>}
          {detailKind === 'hh' ? (
            <HhResumeDetail detail={hhDetail} onSaveContent={saveHhResumeContent} />
          ) : (
            <CvTypeDetail
              detail={cvDetail}
              activeDocument={activeCvDocument}
              onDocumentSelect={setActiveCvDocument}
              onSaveDocument={saveCvTypeDocument}
            />
          )}
        </div>
      </div>
    </div>
  );
}

function HhDiagnosticsPage({ onBack }) {
  const [diagnostics, setDiagnostics] = useState(null);
  const [status, setStatus] = useState('Загружаю диагностическую карту HH...');

  async function refreshDiagnostics() {
    setStatus('Загружаю диагностическую карту HH...');
    try {
      const result = await api('/api/channels/hh/diagnostics');
      setDiagnostics(result);
      setStatus('');
    } catch (error) {
      setStatus(`Не удалось получить диагностику HH: ${error.message}`);
    }
  }

  useEffect(() => {
    refreshDiagnostics();
  }, []);

  const identity = diagnostics?.identity || {};
  const probes = diagnostics?.probes || [];

  return (
    <section className="panel">
      <div className="panel-header">
        <div>
          <h2>Диагностическая карта HH</h2>
          <p className="muted">
            Карта показывает фактический ответ HH API и фиксирует текущее ограничение: резюме и отклики соискателя через API недоступны.
          </p>
        </div>
        <button type="button" className="ghost" onClick={onBack}>Назад к каналам</button>
      </div>

      <div className="channel-actions">
        <div className="button-row">
          <button type="button" onClick={refreshDiagnostics}>Обновить диагностику</button>
        </div>
        {status && <p className="muted">{status}</p>}
      </div>

      {diagnostics && (
        <>
          <div className="diagnostic-grid">
            <StatCard label="Applicant API" value={diagnostics.status?.applicant_api_supported ? 'available' : 'closed'} note="резюме и отклики соискателя" />
            <StatCard label="auth_type" value={identity.auth_type || 'unknown'} note="тип HH OAuth-токена" />
            <StatCard label="HH resumes API" value="closed" note={diagnostics.status?.applicant_api_note || '/resumes/mine недоступен'} />
            <StatCard label="Employer API" value={identity.is_employer ? 'yes' : 'no'} note="контур платных методов" />
          </div>

          {diagnostics.recommendations?.length > 0 && (
            <div className="diagnostic-section">
              <h3>Выводы</h3>
              {diagnostics.recommendations.map((item) => (
                <p className="muted" key={item}>{item}</p>
              ))}
            </div>
          )}

          <div className="diagnostic-section">
            <h3>API-пробы</h3>
            <div className="diagnostic-probes">
              {probes.map((probe) => (
                <div className="diagnostic-card" key={`${probe.label}-${probe.path}`}>
                  <div className="match-row">
                    <strong>{probe.label}</strong>
                    <Pill tone={probe.ok ? 'green' : probe.skipped ? 'default' : 'blue'}>
                      {probe.skipped ? 'skipped' : probe.status_code || 'error'}
                    </Pill>
                  </div>
                  <p className="muted">{probe.path}</p>
                  {probe.note && <p className="muted">{probe.note}</p>}
                  {probe.request_id && <p className="muted">request_id: <code>{probe.request_id}</code></p>}
                  {'body' in probe && <pre>{formatJson(probe.body)}</pre>}
                  {probe.error && <pre>{probe.error}</pre>}
                </div>
              ))}
            </div>
          </div>
        </>
      )}
    </section>
  );
}

function ChannelsPage({ onOpenHhDiagnostics }) {
  const [hhStatus, setHhStatus] = useState(null);
  const [hhError, setHhError] = useState('');
  const [hhConnectStatus, setHhConnectStatus] = useState('');
  const [linkedinStatus, setLinkedinStatus] = useState(null);
  const [linkedinError, setLinkedinError] = useState('');
  const [linkedinConnectStatus, setLinkedinConnectStatus] = useState('');

  async function refreshHhStatus() {
    const status = await api('/api/channels/hh/status');
    setHhStatus(status);
    setHhError('');
    return status;
  }

  async function refreshLinkedInStatus() {
    const status = await api('/api/channels/linkedin/status');
    setLinkedinStatus(status);
    setLinkedinError('');
    return status;
  }

  useEffect(() => {
    refreshHhStatus().catch((error) => setHhError(error.message));
    refreshLinkedInStatus().catch((error) => setLinkedinError(error.message));
  }, []);

  function connectHh() {
    setHhConnectStatus('Открываю HH OAuth в отдельном окне...');
    const authWindow = window.open(`${API_BASE}/api/channels/hh/connect`, 'hh-oauth', 'width=720,height=820');
    if (!authWindow) {
      setHhConnectStatus('Браузер заблокировал окно. Перехожу на HH в текущей вкладке...');
      window.location.href = `${API_BASE}/api/channels/hh/connect`;
      return;
    }

    let attempts = 0;
    const timer = window.setInterval(async () => {
      attempts += 1;
      try {
        const status = await refreshHhStatus();
        if (status.connected) {
          window.clearInterval(timer);
          setHhConnectStatus('HH подключён. Профиль сохранён локально.');
          authWindow.close();
        } else if (authWindow.closed) {
          window.clearInterval(timer);
          setHhConnectStatus('Окно HH закрыто. Если вход завершён, нажмите “Обновить” или попробуйте подключить снова.');
        } else if (attempts > 90) {
          window.clearInterval(timer);
          setHhConnectStatus('Время ожидания истекло. Проверьте окно HH или попробуйте подключить снова.');
        }
      } catch (error) {
        setHhError(error.message);
      }
    }, 2000);
  }

  function connectLinkedIn() {
    setLinkedinConnectStatus('Открываю LinkedIn OAuth в отдельном окне...');
    const authWindow = window.open(`${API_BASE}/api/channels/linkedin/connect`, 'linkedin-oauth', 'width=720,height=820');
    if (!authWindow) {
      setLinkedinConnectStatus('Браузер заблокировал окно. Перехожу на LinkedIn в текущей вкладке...');
      window.location.href = `${API_BASE}/api/channels/linkedin/connect`;
      return;
    }

    let attempts = 0;
    const timer = window.setInterval(async () => {
      attempts += 1;
      try {
        const status = await refreshLinkedInStatus();
        if (status.connected) {
          window.clearInterval(timer);
          setLinkedinConnectStatus('LinkedIn подключён. Профиль сохранён локально.');
          authWindow.close();
        } else if (authWindow.closed) {
          window.clearInterval(timer);
          setLinkedinConnectStatus('Окно LinkedIn закрыто. Если вход завершён, нажмите “Обновить” или попробуйте подключить снова.');
        } else if (attempts > 90) {
          window.clearInterval(timer);
          setLinkedinConnectStatus('Время ожидания истекло. Проверьте окно LinkedIn или попробуйте подключить снова.');
        }
      } catch (error) {
        setLinkedinError(error.message);
      }
    }, 2000);
  }

  const channels = [
    {
      title: 'HH',
      status: 'локальный режим',
      description: 'HH используем как источник вакансий, писем-событий и локально импортированных резюме. Соискательский API для резюме и откликов закрыт.',
      hh: true,
    },
    {
      title: 'Telegram',
      status: 'запланировано',
      description: 'Несколько каналов: вакансии, HR-посты, целевые подборки, ручные заметки. Позже добавим типизацию каналов.',
    },
    {
      title: 'LinkedIn',
      status: linkedinStatus?.connected ? 'подключён' : 'OAuth готовится',
      description: 'OpenID Connect для базового профиля, ручной импорт вакансий и локальный трекинг откликов без Talent API.',
      linkedin: true,
    },
    {
      title: 'Другие источники',
      status: 'резерв',
      description: 'CSV, email, карьерные сайты компаний, ручной импорт вакансий и заметок.',
    },
  ];

  return (
    <section className="panel">
      <h2>Каналы данных</h2>
      <p className="muted">Каналы отделены от workflow: источник может быть любым, а обработка сигналов остается общей.</p>
      <div className="channel-grid">
        {channels.map((channel) => (
          <div className="channel-card" key={channel.title}>
            <div className="match-row">
              <strong>{channel.title}</strong>
              <Pill tone={(channel.hh && hhStatus?.connected) || (channel.linkedin && linkedinStatus?.connected) ? 'blue' : 'default'}>
                {channel.status}
              </Pill>
            </div>
            <p className="muted">{channel.description}</p>
            {channel.hh && (
              <div className="channel-actions">
                {hhError && <p className="muted">Не удалось проверить HH: {hhError}</p>}
                {hhStatus && !hhStatus.configured && (
                  <p className="muted">
                    Не хватает переменных: {hhStatus.missing.join(', ')}. Redirect URI: {hhStatus.redirect_uri}
                  </p>
                )}
                {hhStatus?.configured && !hhStatus.connected && (
                  <p className="muted">
                    OAuth-подключение больше не требуется для резюме/откликов. Redirect URI оставлен для диагностики и будущего контура вакансий: {hhStatus.redirect_uri}
                  </p>
                )}
                {hhStatus?.connected && (
                  <p className="muted">
                    Исторически подключенный профиль: <strong>{hhStatus.account?.name || 'HH'}</strong>
                    {hhStatus.account?.email ? ` · ${hhStatus.account.email}` : ''}
                  </p>
                )}
                {hhStatus?.connected && !hhStatus?.token_saved && (
                  <p className="muted">
                    Профиль подключён старым OAuth-flow. Для текущего локального режима переподключение не требуется.
                  </p>
                )}
                <p className="muted">
                  Резюме ведём через импорт файлов и редактор внутри Resume Intel. Отклики и просмотры собираем из писем HH.
                </p>
                {hhConnectStatus && <p className="muted">{hhConnectStatus}</p>}
                <div className="button-row">
                  <button type="button" onClick={connectHh} disabled={!hhStatus?.configured}>
                    {hhStatus?.connected ? 'Обновить OAuth HH' : 'Подключить HH OAuth'}
                  </button>
                  <button type="button" className="ghost" onClick={onOpenHhDiagnostics}>
                    Диагностика HH
                  </button>
                </div>
              </div>
            )}
            {channel.linkedin && (
              <div className="channel-actions">
                {linkedinError && <p className="muted">Не удалось проверить LinkedIn: {linkedinError}</p>}
                {linkedinStatus && !linkedinStatus.configured && (
                  <p className="muted">
                    Не хватает переменных: {linkedinStatus.missing.join(', ')}. Redirect URI: {linkedinStatus.redirect_uri}
                  </p>
                )}
                {linkedinStatus?.connected && (
                  <div className="linkedin-profile">
                    {linkedinStatus.account?.picture_url && <img alt="" src={linkedinStatus.account.picture_url} />}
                    <div>
                      <strong>{linkedinStatus.account?.name || 'LinkedIn profile'}</strong>
                      {linkedinStatus.account?.email && <div className="muted">{linkedinStatus.account.email}</div>}
                      {linkedinStatus.account?.profile_id && <div className="muted">LinkedIn subject: {linkedinStatus.account.profile_id}</div>}
                      {linkedinStatus.account?.updated_at && (
                        <div className="muted">Обновлено: {formatDateTime(linkedinStatus.account.updated_at)}</div>
                      )}
                    </div>
                  </div>
                )}
                <p className="muted">
                  OpenID Connect отдаёт только базовый профиль. Полные CV из LinkedIn импортируются файлом на вкладке “Резюме”.
                </p>
                {linkedinConnectStatus && <p className="muted">{linkedinConnectStatus}</p>}
                <button type="button" onClick={connectLinkedIn} disabled={!linkedinStatus?.configured}>
                  {linkedinStatus?.connected ? 'Переподключить LinkedIn' : 'Подключить LinkedIn'}
                </button>
              </div>
            )}
          </div>
        ))}
      </div>
    </section>
  );
}

function App() {
  const [events, setEvents] = useState([]);
  const [cvTypes, setCvTypes] = useState([]);
  const [hhResumes, setHhResumes] = useState([]);
  const [vacancies, setVacancies] = useState([]);
  const [hhStatus, setHhStatus] = useState(null);
  const [linkedinStatus, setLinkedinStatus] = useState(null);
  const [selectedId, setSelectedId] = useState(null);
  const [activePage, setActivePage] = useState('overview');
  const selected = useMemo(
    () => events.find((event) => event.id === selectedId) || events[0],
    [events, selectedId],
  );
  const stats = useMemo(
    () => ({ events: events.length, cvTypes: cvTypes.length, hhResumes: hhResumes.length }),
    [events.length, cvTypes.length, hhResumes.length],
  );

  async function refresh() {
    const [nextEvents, nextTypes, nextHhResumes, nextVacancies, nextHhStatus, nextLinkedinStatus] = await Promise.all([
      api('/api/events'),
      api('/api/cv-types'),
      api('/api/hh-resumes'),
      api('/api/vacancies'),
      api('/api/channels/hh/status'),
      api('/api/channels/linkedin/status'),
    ]);
    setEvents(nextEvents);
    setCvTypes(nextTypes);
    setHhResumes(nextHhResumes);
    setVacancies(nextVacancies);
    setHhStatus(nextHhStatus);
    setLinkedinStatus(nextLinkedinStatus);
  }

  useEffect(() => {
    refresh().catch((error) => console.error(error));
  }, []);

  function handleImported(event) {
    setEvents((current) => [event, ...current.filter((item) => item.id !== event.id)]);
    setSelectedId(event.id);
    setActivePage('attention');
  }

  function renderPage() {
    if (activePage === 'attention') {
      return (
        <AttentionPage
          events={events}
          selected={selected}
          setSelectedId={setSelectedId}
          onImported={handleImported}
          onChanged={refresh}
        />
      );
    }
    if (activePage === 'workflow') {
      return <WorkflowPage events={events} vacancies={vacancies} selected={selected} setSelectedId={setSelectedId} onChanged={refresh} />;
    }
    if (activePage === 'resumes') {
      return <ResumesPage hhResumes={hhResumes} cvTypes={cvTypes} hhStatus={hhStatus} onImported={refresh} />;
    }
    if (activePage === 'channels') {
      return <ChannelsPage onOpenHhDiagnostics={() => setActivePage('hh-diagnostics')} />;
    }
    if (activePage === 'hh-diagnostics') {
      return <HhDiagnosticsPage onBack={() => setActivePage('channels')} />;
    }
    return <OverviewPage events={events} cvTypes={cvTypes} hhResumes={hhResumes} onNavigate={setActivePage} />;
  }

  return (
    <div className="app-shell">
      <Sidebar activePage={activePage} onNavigate={setActivePage} stats={stats} />
      <main className="workspace">
        <TopBar activePage={activePage} onRefresh={refresh} />
        {renderPage()}
      </main>
    </div>
  );
}

createRoot(document.getElementById('root')).render(<App />);
