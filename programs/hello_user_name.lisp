(setq buf-base 1792)
(setq length 0)
(setq done 0)

(defun _interrupt_handler ()
  (begin
    (setq c (read))
    (if (= c 10)
        (setq done 1)
        (begin
          (memset (+ buf-base length) c)
          (setq length (+ length 1))))))

(print "What is your name?
")

(ei)

(loop (while (= done 0)) 0)

(di)

(print "Hello, ")

(setq i 0)
(loop (while (< i length))
  (begin
    (print-char (memref (+ buf-base i)))
    (setq i (+ i 1))))

(print "!
")
