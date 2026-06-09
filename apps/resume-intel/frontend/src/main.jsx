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
          <div className="muted">{item.slug}</div>
        </button>
      ))}
    </section>
  );
}

function HhResumes({ resumes, selectedId, onSelect }) {
  return (
    <section className="panel cv-types">
      <h2>Текущие HH-резюме</h2>
      {resumes.length === 0 ? (
        <p className="muted">Справочник пуст. Импортируйте PDF/RTF/HTML/TXT из раздела “Мои резюме” HH.</p>
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
              {item.external_id ? ` · HH ID ${item.external_id}` : ''}
              {item.source_filename ? ` · ${item.source_filename}` : ''}
            </div>
            {item.keywords?.length > 0 && <p className="terms">{item.keywords.slice(0, 8).join(', ')}</p>}
          </button>
        ))
      )}
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
        <p>{value}</p>
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

function HhResumeDetail({ detail }) {
  if (!detail) {
    return (
      <section className="panel detail empty">
        <h2>Выберите HH-резюме</h2>
        <p className="muted">Здесь будет полный текст импортированного резюме, метаданные, source-файл и keywords.</p>
      </section>
    );
  }

  return (
    <section className="panel detail">
      <div className="panel-header">
        <h2>{detail.title}</h2>
        <Pill tone="green">{detail.channel || 'hh'}</Pill>
      </div>
      <div className="grid">
        <DetailMeta label="Статус" value={detail.status} />
        <DetailMeta label="HH ID" value={detail.external_id} />
        <DetailMeta label="Файл" value={detail.source_filename} />
        <DetailMeta label="Обновлено" value={detail.updated_at} />
        <DetailMeta label="Импортов" value={detail.import_count ? String(detail.import_count) : ''} />
        <DetailMeta label="URL" value={detail.url} />
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
      <ResumeStructure structure={detail.parsed_structure} />
      <h3>Содержимое резюме</h3>
      <pre className="document-viewer">{detail.raw_text || 'Текст резюме не сохранен.'}</pre>
    </section>
  );
}

function CvTypeStructure({ detail, selectedDocument }) {
  const currentSections = selectedDocument?.sections || [];

  if (!detail?.structure) return null;

  return (
    <div className="structure-view">
      <div className="panel-header">
        <h2>Секции текущего документа</h2>
        <Pill>{currentSections.length} секций</Pill>
      </div>
      {currentSections.length > 0 && (
        <div className="matches">
          {currentSections.map((section) => (
            <div className="match-card" key={`${selectedDocument.filename}-${section.title}`}>
              <div className="match-row">
                <strong>{section.title}</strong>
                <Pill>{section.kind}</Pill>
              </div>
              {section.bullets?.length > 0 ? (
                <ul>
                  {section.bullets.map((bullet) => (
                    <li key={bullet}>{bullet}</li>
                  ))}
                </ul>
              ) : (
                <p className="terms">{section.content?.slice(0, 500)}</p>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function CvTypeDetail({ detail, activeDocument, onDocumentSelect }) {
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
      <CvTypeStructure detail={detail} selectedDocument={selectedDocument} />
      <h3>{selectedDocument?.title || 'Документ'}</h3>
      <pre className="document-viewer">{selectedDocument?.content || 'Документ не найден.'}</pre>
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
          <div className="eyebrow">Редкая операция</div>
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
        <strong>Заявка на рассмотрении</strong>
        <p className="muted">Пока основной источник — Apple Mail import. После одобрения добавим OAuth и синхронизацию.</p>
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

function WorkflowPage({ events, selected, setSelectedId, onChanged }) {
  const companies = Array.from(new Set(events.map((event) => event.company_name).filter(Boolean)));

  return (
    <div className="content-layout">
      <section className="panel event-list">
        <div className="panel-header">
          <h2>Pipeline компаний</h2>
          <Pill>{companies.length}</Pill>
        </div>
        {companies.length === 0 ? (
          <p className="muted">Компании появятся после импорта HH-писем или будущей синхронизации HH API.</p>
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
          Сейчас можно вручную добавить текст вакансии, чтобы уточнить matching.
        </p>
        {selected ? <VacancyForm company={selected.company_name} onSaved={onChanged} /> : null}
        {selected ? <Detail event={selected} onChanged={onChanged} /> : null}
      </section>
    </div>
  );
}

function ResumesPage({ hhResumes, cvTypes, onImported }) {
  const [detailKind, setDetailKind] = useState('hh');
  const [selectedHhId, setSelectedHhId] = useState('');
  const [selectedCvSlug, setSelectedCvSlug] = useState('');
  const [hhDetail, setHhDetail] = useState(null);
  const [cvDetail, setCvDetail] = useState(null);
  const [activeCvDocument, setActiveCvDocument] = useState('');
  const [detailStatus, setDetailStatus] = useState('');

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

  return (
    <div className="page-grid">
      <ResumeImportBox resumes={hhResumes} onImported={onImported} />
      <div className="content-layout">
        <div>
          <HhResumes resumes={hhResumes} selectedId={selectedHhId} onSelect={selectHhResume} />
          <CvTypes cvTypes={cvTypes} selectedId={selectedCvSlug} onSelect={selectCvType} />
        </div>
        <div>
          {detailStatus && <p className="muted">{detailStatus}</p>}
          {detailKind === 'hh' ? (
            <HhResumeDetail detail={hhDetail} />
          ) : (
            <CvTypeDetail detail={cvDetail} activeDocument={activeCvDocument} onDocumentSelect={setActiveCvDocument} />
          )}
        </div>
      </div>
    </div>
  );
}

function ChannelsPage() {
  const channels = [
    {
      title: 'HH',
      status: 'заявка API на рассмотрении',
      description: 'OAuth, вакансии работодателя, отклики, резюме и доступные события по API. До подключения работает импорт писем из Apple Mail.',
    },
    {
      title: 'Telegram',
      status: 'запланировано',
      description: 'Несколько каналов: вакансии, HR-посты, целевые подборки, ручные заметки. Позже добавим типизацию каналов.',
    },
    {
      title: 'LinkedIn',
      status: 'запланировано',
      description: 'Импорт сигналов, сообщений и вакансий там, где это технически и юридически допустимо.',
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
              <Pill tone={channel.title === 'HH' ? 'blue' : 'default'}>{channel.status}</Pill>
            </div>
            <p className="muted">{channel.description}</p>
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
    const [nextEvents, nextTypes, nextHhResumes] = await Promise.all([
      api('/api/events'),
      api('/api/cv-types'),
      api('/api/hh-resumes'),
    ]);
    setEvents(nextEvents);
    setCvTypes(nextTypes);
    setHhResumes(nextHhResumes);
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
      return <WorkflowPage events={events} selected={selected} setSelectedId={setSelectedId} onChanged={refresh} />;
    }
    if (activePage === 'resumes') {
      return <ResumesPage hhResumes={hhResumes} cvTypes={cvTypes} onImported={refresh} />;
    }
    if (activePage === 'channels') {
      return <ChannelsPage />;
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
