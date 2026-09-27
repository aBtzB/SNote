# Making this a project you can stand behind

The strongest version of this project is one you can demonstrate, change, and explain. An original interface helps, but your decisions and your evaluation give it substance.

## A sensible project question

“Can a small local tool make it easier to turn recorded interviews or lectures into a corrected bilingual transcript?”

That is specific enough to test. Choose the audience that fits your actual work: student interviews, lecture notes, language study, or short research recordings. Replace the sample with a short recording you made or have permission to use, and label it accurately.

## Understand these parts first

1. Follow one upload from `server.py` into `jobs.py` and `engines.py`.
2. Explain why transcription and translation are separate stages.
3. Change one piece of source text and watch its old translation become invalid.
4. Open the same recording in two tabs, save from one, and show the conflict in the other.
5. Open an SRT export and explain why the timestamps use milliseconds.
6. Read the test doubles. Explain what they prove about the app, and what they do not prove about recognition accuracy.
7. Trace a model download through its temporary directory, checks, and final installation. Explain why opening Models does not download anything.

## Make one meaningful extension

Pick a feature you actually need and implement it yourself. Good candidates are editable speaker labels, a project-specific name glossary, a segment split/merge editor, or a comparison of Tiny and Base on the same audio. Keep the scope small enough to finish, test, and discuss. Add a brief decision note describing the actual change and its result.

A useful evaluation can be five recordings with different conditions. Record the duration, language, model, processing time, important transcription mistakes, and corrections you made. Use measured results, and keep the underlying reference transcripts. Do not turn guesses into accuracy or speed claims.

## A short demo

Keep it to about three minutes: upload a short clip, show the source transcript and translation, play a timestamp, correct one error, save, re-translate, mark a line reviewed, and export subtitles. Finish by showing one test that protects a behavior you care about.

If model processing takes too long during the demo, use a previously processed recording and say so. The illustrative sample is useful for exploring the editor; it is not evidence of model output.

## CV wording

After you have run, understood, and extended the project, a starting point is:

> Developed a local audio review application in Python and JavaScript, integrating Whisper transcription and local translation models with timestamped editing, SQLite persistence, and subtitle exports.

Adjust that to your actual contribution. Add your own extension and measured evaluation if you have them. The engineering contribution is the application and its behavior; the recognition and translation models come from their respective open-source projects.

For an interview, be ready to answer: Why local models? What happens if translation fails? How do edits stay consistent across tabs? What would you change for multiple users? How did you check the output on real recordings? Describe assistance and reused components accurately when asked or when your project's rules require it.

## Present it plainly

Use your own project name if you prefer. Keep acknowledgments. Add actual screenshots once you run it, an example export, and a short explanation of a bug you found and fixed. There is no need for fabricated commit history, pretend users, or a list of technologies the project does not use.
