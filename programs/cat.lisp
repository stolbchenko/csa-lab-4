(setq done 0)

(defun _interrupt_handler ()
  (begin
    (setq c (read))
    (if (= c 88)
        (setq done 1)
        (print-char c))))

(ei)

(loop (while (= done 0))
  0)

(di)
